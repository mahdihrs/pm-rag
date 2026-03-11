#!/usr/bin/env python3
"""
query.py — Hybrid search RAG for PM knowledge base.

Search modes:
  - Semantic search  → finds conceptually related content (existing)
  - Keyword search   → finds exact/partial/regex matches (new)
  - Hybrid (default) → runs both, merges and re-ranks results

Hallucination safeguards:
  1. Temperature = 0        → fully deterministic
  2. Similarity threshold   → refuses if no relevant docs found
  3. Strict grounded prompt → LLM never uses outside knowledge
  4. Conflict detection     → surfaces contradictions explicitly
  5. Mandatory source footer → every answer ends with doc names
  6. UNSURE flagging        → uncertain claims are marked

Usage:
    python query.py                    # Interactive hybrid search
    python query.py "Your question"    # Single question, then exit
    python query.py --sync             # Re-sync docs before starting
    python query.py --mode semantic    # Force semantic-only
    python query.py --mode keyword     # Force keyword-only
"""

import os
import re
import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich import box

load_dotenv()
console = Console()

# ─── Config ───────────────────────────────────────────────────────────────────

# def load_config() -> dict:
#     with open("config.yaml") as f:
#         return yaml.safe_load(f)
def load_config() -> dict:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    # Override with local private config if it exists
    local_cfg_path = Path("config.local.yaml")
    if local_cfg_path.exists():
        with open(local_cfg_path) as f:
            local_cfg = yaml.safe_load(f)
        config.update(local_cfg)

    return config

# ─── Sync ─────────────────────────────────────────────────────────────────────

def run_sync(config: dict):
    from ingest import run_ingest, load_sync_state
    state = load_sync_state()
    last = state.get("last_sync")
    if last:
        console.print(f"[dim]Last sync: {last[:19].replace('T', ' ')} UTC[/dim]")
    with console.status("[cyan]🔄 Syncing documents...[/cyan]"):
        added, skipped, total = run_ingest(config)
    if added > 0:
        console.print(f"[green]✅ Sync done — {added} new chunks added, {total} total.[/green]")
    else:
        console.print(f"[green]✅ Already up to date.[/green]")

# ─── Vector Store ─────────────────────────────────────────────────────────────

def get_collection(config: dict):
    import chromadb
    from chromadb.utils import embedding_functions

    db_path = config["vector_store"]["path"]
    if not Path(db_path).exists():
        console.print("[red]❌ Vector store not found. Run [bold]python ingest.py[/bold] first.[/red]")
        sys.exit(1)

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config["embeddings"]["model"]
    )
    client = chromadb.PersistentClient(path=db_path)
    return client.get_collection(name="pm_docs", embedding_function=ef)

# ─── Semantic Search ──────────────────────────────────────────────────────────

def semantic_search(
    query: str,
    collection,
    top_k: int = 10,
) -> List[Tuple[str, str, float, str]]:
    """
    Embedding-based similarity search.
    Returns list of (content, source, score, match_type).
    Score is cosine similarity 0–1, higher = more relevant.
    """
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        similarity = 1.0 - (dist / 2.0)
        source = meta.get("filename") or meta.get("source") or "Unknown"
        chunks.append((doc, source, similarity, "semantic"))

    return chunks

# ─── Keyword Search ───────────────────────────────────────────────────────────

def build_keyword_pattern(query: str) -> re.Pattern:
    """
    Build a flexible regex from the query that handles:
    - Partial matches:  "sync" matches "synchronization", "syncing", "synced"
    - Case insensitive: "prd" matches "PRD", "Prd"
    - Multi-word:       "user auth" matches chunks containing both words
    - Special chars:    safely escaped
    """
    # Split into individual terms, strip punctuation
    terms = [re.escape(t.strip()) for t in query.split() if t.strip()]

    if not terms:
        return re.compile(r"(?!)")  # Never matches

    # Each term becomes a word-boundary partial match
    # "sync" → matches "sync", "syncing", "synchronized", "resync" etc.
    term_patterns = [rf"\b{term}" for term in terms]

    # All terms must appear somewhere in the chunk (AND logic)
    combined = "(?=.*" + ")(?=.*".join(term_patterns) + ")"
    return re.compile(combined, re.IGNORECASE | re.DOTALL)

def keyword_score(content: str, pattern: re.Pattern, query: str) -> float:
    """
    Score a chunk based on keyword match quality.
    Returns 0.0–1.0 where:
      1.0 = exact full query match
      0.7 = all terms present
      0.4 = partial term matches
    """
    terms = query.lower().split()
    content_lower = content.lower()

    # Exact full phrase match
    if query.lower() in content_lower:
        return 1.0

    # Count how many terms match (partial prefix match)
    matched = sum(
        1 for term in terms
        if re.search(rf"\b{re.escape(term)}", content_lower)
    )

    if matched == len(terms):
        return 0.75  # All terms found

    if matched > 0:
        return 0.4 * (matched / len(terms))  # Partial match

    return 0.0

def keyword_search(
    query: str,
    collection,
    top_k: int = 10,
) -> List[Tuple[str, str, float, str]]:
    """
    Brute-force regex scan across all indexed chunks.
    Returns list of (content, source, score, match_type).
    """
    pattern = build_keyword_pattern(query)

    # Fetch all chunks from the DB
    all_data = collection.get(include=["documents", "metadatas"])
    docs = all_data["documents"]
    metas = all_data["metadatas"]

    matches = []
    for doc, meta in zip(docs, metas):
        if pattern.search(doc):
            score = keyword_score(doc, pattern, query)
            if score > 0:
                source = meta.get("filename") or meta.get("source") or "Unknown"
                matches.append((doc, source, score, "keyword"))

    # Return top-k by score
    matches.sort(key=lambda x: x[2], reverse=True)
    return matches[:top_k]

# ─── Hybrid Merge & Re-rank ───────────────────────────────────────────────────

def hybrid_search(
    query: str,
    collection,
    top_k: int = 10,
    similarity_threshold: float = 0.35,
    semantic_weight: float = 0.6,
    keyword_weight: float = 0.4,
) -> Tuple[List[Tuple[str, str, float, str]], bool]:
    """
    Runs semantic + keyword search in parallel, merges results using
    Reciprocal Rank Fusion (RRF) weighted by semantic/keyword importance.

    Returns (chunks, is_relevant):
      chunks = [(content, source, final_score, match_type), ...]
      is_relevant = False if no chunk clears the threshold
    """
    sem_results = semantic_search(query, collection, top_k=top_k * 2)
    kw_results  = keyword_search(query, collection, top_k=top_k * 2)

    # ── Reciprocal Rank Fusion ──
    # Each chunk gets a score based on its rank in each list.
    # RRF formula: score = Σ weight / (rank + k) where k=60 dampens outliers.
    K = 60
    scores: dict = {}   # content_hash → running score
    chunk_map: dict = {}  # content_hash → (content, source, match_types)

    def rrf_score(rank: int, weight: float) -> float:
        return weight / (rank + K)

    for rank, (content, source, raw_score, mtype) in enumerate(sem_results):
        key = hash(content[:200])  # Use first 200 chars as identity
        scores[key] = scores.get(key, 0) + rrf_score(rank, semantic_weight)
        if key not in chunk_map:
            chunk_map[key] = (content, source, set())
        chunk_map[key][2].add(mtype)

    for rank, (content, source, raw_score, mtype) in enumerate(kw_results):
        key = hash(content[:200])
        scores[key] = scores.get(key, 0) + rrf_score(rank, keyword_weight)
        if key not in chunk_map:
            chunk_map[key] = (content, source, set())
        chunk_map[key][2].add(mtype)

    # ── Normalize scores to 0–1 range ──
    max_score = max(scores.values()) if scores else 1.0
    normalized = {k: v / max_score for k, v in scores.items()}

    # ── Build final ranked list ──
    ranked = []
    for key, (content, source, mtypes) in chunk_map.items():
        final_score = normalized.get(key, 0)
        match_label = "+".join(sorted(mtypes))  # e.g. "keyword", "semantic", "keyword+semantic"
        ranked.append((content, source, final_score, match_label))

    ranked.sort(key=lambda x: x[2], reverse=True)
    ranked = ranked[:top_k]

    best_score = ranked[0][2] if ranked else 0.0
    is_relevant = best_score >= similarity_threshold

    return ranked, is_relevant

# ─── Prompt Engineering ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a precise, fact-only assistant for a Product Manager. Your ONLY job is to report what is written in the provided source documents. You must never use knowledge from your training data.

STRICT RULES — follow every one without exception:

1. ONLY use information explicitly stated in the provided sources. Never infer, assume, or fill gaps.
2. If the sources do not contain enough information to answer fully, say exactly:
   "INSUFFICIENT DATA: The available documents do not contain enough information to answer this fully."
   Then state only what IS available.
3. If you are uncertain about any specific claim, prefix that claim with "UNSURE:" so the user knows to verify it.
4. CONFLICT RULE (critical): If two or more sources state different or contradictory information about the same fact, you MUST surface both versions. Use this exact format:
   ⚠️ CONFLICTING INFORMATION FOUND:
   - [Document A name]: states "..."
   - [Document B name]: states "..."
   Do not pick one over the other. Present both and let the user decide.
5. At the END of every response, always include a sources footer in exactly this format:
   ---
   📄 Sources: [Doc1.pdf], [Doc2.docx], [Doc3.md]
   Do not skip this footer even for short answers.
6. Never say "based on my knowledge", "I believe", "likely", "probably", or any hedging phrase not tied to a specific document.
7. If asked about something completely absent from the documents, respond only with:
   "NOT FOUND: No information about this topic exists in your indexed documents."
"""

def build_prompt(question: str, chunks: List[Tuple[str, str, float, str]]) -> str:
    source_blocks: dict = {}
    for content, source, score, mtype in chunks:
        name = Path(source).name if ("/" in source or "\\" in source) else source
        name = name.replace("GoogleDrive:", "")
        if name not in source_blocks:
            source_blocks[name] = []
        source_blocks[name].append(content)

    context_parts = []
    for doc_name, passages in source_blocks.items():
        combined = "\n\n".join(passages)
        context_parts.append(f"=== DOCUMENT: {doc_name} ===\n{combined}")

    context = "\n\n".join(context_parts)

    return f"""{SYSTEM_PROMPT}

--- SOURCE DOCUMENTS ---
{context}
--- END SOURCES ---

Question: {question}

Answer (cite sources inline, flag conflicts with ⚠️, end with 📄 Sources footer):"""

# ─── LLM Callers ──────────────────────────────────────────────────────────────

def ask_anthropic(prompt: str, config: dict) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    llm = config["llm"]
    resp = client.messages.create(
        model=llm.get("model", "claude-sonnet-4-20250514"),
        max_tokens=llm.get("max_tokens", 2048),
        temperature=llm.get("temperature", 0),
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text

def ask_openai(prompt: str, config: dict) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    llm = config["llm"]
    resp = client.chat.completions.create(
        model=llm.get("model", "gpt-4o"),
        temperature=llm.get("temperature", 0),
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content

def ask_ollama(prompt: str, config: dict) -> str:
    import requests
    llm = config["llm"]
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": llm.get("model", "mistral"),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=120,
    )
    return resp.json()["response"]

def get_answer(question: str, chunks: List[Tuple[str, str, float, str]], config: dict) -> str:
    prompt = build_prompt(question, chunks)
    provider = config["llm"].get("provider", "anthropic")
    if provider == "anthropic":
        return ask_anthropic(prompt, config)
    elif provider == "openai":
        return ask_openai(prompt, config)
    elif provider == "ollama":
        return ask_ollama(prompt, config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

# ─── Display ──────────────────────────────────────────────────────────────────

def display_answer(
    answer: str,
    chunks: List[Tuple[str, str, float, str]],
    best_score: float,
    mode: str,
):
    console.print()

    has_conflict  = "⚠️ CONFLICTING" in answer
    has_unsure    = "UNSURE:" in answer
    has_not_found = "NOT FOUND:" in answer or "INSUFFICIENT DATA:" in answer

    if has_conflict:
        border, title = "yellow", "[bold yellow]Answer — ⚠️ Conflicts Detected[/bold yellow]"
    elif has_not_found:
        border, title = "red", "[bold red]Answer — Insufficient Data[/bold red]"
    elif has_unsure:
        border, title = "orange3", "[bold orange3]Answer — Contains Uncertain Claims[/bold orange3]"
    else:
        border, title = "cyan", "[bold cyan]Answer[/bold cyan]"

    console.print(Panel(Markdown(answer), title=title, border_style=border, padding=(1, 2)))

    # ── Confidence bar ──
    bar_width = 20
    filled = int(best_score * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    score_pct = int(best_score * 100)
    score_color = "green" if best_score >= 0.75 else "yellow" if best_score >= 0.4 else "red"
    mode_label = {"hybrid": "🔀 Hybrid", "semantic": "🧠 Semantic", "keyword": "🔍 Keyword"}.get(mode, mode)
    console.print(
        f"  [dim]Search mode:[/dim] [cyan]{mode_label}[/cyan]  "
        f"[dim]Confidence:[/dim] [{score_color}]{bar} {score_pct}%[/{score_color}]"
    )

    # ── Source table ──
    table = Table(title="Retrieved Documents", box=box.SIMPLE, border_style="dim", show_lines=False)
    table.add_column("Document", style="green", no_wrap=False)
    table.add_column("Match", justify="center", width=12)
    table.add_column("Score", justify="right", width=7)

    seen = set()
    for _, source, score, mtype in chunks:
        name = Path(source).name if ("/" in source or "\\" in source) else source
        name = name.replace("GoogleDrive:", "")
        if name not in seen:
            seen.add(name)
            pct = f"{int(score * 100)}%"
            score_col = "green" if score >= 0.75 else "yellow" if score >= 0.4 else "dim"
            # Match type badge
            if "keyword+semantic" in mtype or "semantic+keyword" in mtype:
                badge = "[green]both ✓[/green]"
            elif "keyword" in mtype:
                badge = "[yellow]keyword[/yellow]"
            else:
                badge = "[cyan]semantic[/cyan]"
            table.add_row(name, badge, f"[{score_col}]{pct}[/{score_col}]")

    console.print(table)
    console.print()

def display_refusal(best_score: float, threshold: float):
    console.print()
    console.print(Panel(
        f"[bold]NOT FOUND[/bold]\n\n"
        f"No documents matched your query reliably enough to answer.\n\n"
        f"Best match score: [red]{int(best_score * 100)}%[/red]  "
        f"(minimum: [yellow]{int(threshold * 100)}%[/yellow])\n\n"
        f"[dim]Tips:\n"
        f"• Try different keywords or phrasing\n"
        f"• Use [bold]/mode keyword[/bold] to force keyword-only search\n"
        f"• Lower [bold]similarity_threshold[/bold] in config.yaml[/dim]",
        title="[bold red]⛔ No Confident Match[/bold red]",
        border_style="red",
        padding=(1, 2),
    ))
    console.print()

def show_sync_status():
    try:
        from ingest import load_sync_state
        state = load_sync_state()
        if state.get("last_sync"):
            last = state["last_sync"][:19].replace("T", " ")
            console.print(f"[dim]🕒 Last synced: {last} UTC  |  /sync to refresh[/dim]")
    except Exception:
        pass

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Query your PM knowledge base")
    parser.add_argument("question", nargs="?", help="Ask a single question and exit")
    parser.add_argument("--sync", action="store_true", help="Re-sync docs before querying")
    parser.add_argument(
        "--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid",
        help="Search mode (default: hybrid)"
    )
    args = parser.parse_args()

    config = load_config()
    retrieval_cfg = config.get("retrieval", {})
    top_k = retrieval_cfg.get("top_k", 10)
    similarity_threshold = retrieval_cfg.get("similarity_threshold", 0.35)

    console.print(Panel.fit(
        "[bold cyan]PM RAG — Knowledge Assistant[/bold cyan]\n"
        "[dim]Hybrid search • Grounded answers • Temperature 0[/dim]",
        border_style="cyan"
    ))

    provider = config["llm"].get("provider", "anthropic")
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]❌ ANTHROPIC_API_KEY not set in .env[/red]")
        sys.exit(1)
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        console.print("[red]❌ OPENAI_API_KEY not set in .env[/red]")
        sys.exit(1)

    if args.sync:
        run_sync(config)
        console.print()

    collection = get_collection(config)
    doc_count = collection.count()
    mode = args.mode

    show_sync_status()
    console.print(
        f"[dim]📚 {doc_count} chunks  |  "
        f"Mode: [cyan]{mode}[/cyan]  |  "
        f"[bold]exit[/bold] to quit  |  "
        f"[bold]/sync[/bold] to refresh  |  "
        f"[bold]/mode hybrid|semantic|keyword[/bold] to switch[/dim]\n"
    )

    def process_question(question: str, current_mode: str):
        with console.status(f"[cyan]Searching ({current_mode})...[/cyan]"):
            if current_mode == "semantic":
                raw = semantic_search(question, collection, top_k)
                best = raw[0][2] if raw else 0.0
                is_relevant = best >= similarity_threshold
                chunks = raw
            elif current_mode == "keyword":
                raw = keyword_search(question, collection, top_k)
                best = raw[0][2] if raw else 0.0
                is_relevant = best > 0
                chunks = raw
            else:  # hybrid
                chunks, is_relevant = hybrid_search(
                    question, collection, top_k, similarity_threshold
                )
                best = chunks[0][2] if chunks else 0.0

        if not is_relevant or not chunks:
            display_refusal(best, similarity_threshold)
            return

        with console.status("[cyan]Generating grounded answer...[/cyan]"):
            try:
                answer = get_answer(question, chunks, config)
            except Exception as e:
                console.print(f"[red]LLM error: {e}[/red]")
                return

        display_answer(answer, chunks, best, current_mode)

    # ── Single-question mode ──
    if args.question:
        process_question(args.question, mode)
        return

    # ── Interactive mode ──
    console.print("[dim]💡 Try: 'sync feature', 'PRD authentication', 'Q1 MoM action items'[/dim]\n")

    while True:
        try:
            question = console.input("[bold green]You:[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Goodbye![/dim]")
            break

        # ── /sync command ──
        if question.lower() == "/sync":
            run_sync(config)
            collection = get_collection(config)
            console.print(f"[dim]📚 Now {collection.count()} chunks indexed[/dim]\n")
            continue

        # ── /mode switch ──
        if question.lower().startswith("/mode"):
            parts = question.split()
            if len(parts) == 2 and parts[1] in ("hybrid", "semantic", "keyword"):
                mode = parts[1]
                console.print(f"[cyan]Switched to {mode} search mode.[/cyan]\n")
            else:
                console.print("[yellow]Usage: /mode hybrid | semantic | keyword[/yellow]\n")
            continue

        process_question(question, mode)

if __name__ == "__main__":
    main()