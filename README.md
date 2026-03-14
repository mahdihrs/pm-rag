# PM RAG — Terminal Knowledge Assistant

Ask questions about your projects, MoMs, and docs directly from your Mac terminal.

---

## Quickstart (5 steps)

### 1. Install dependencies
```bash
cd pm-rag
pip install -r requirements.txt
```

### 2. Set your API key
```bash
cp .env.example .env
# Then open .env and paste your Anthropic API key
# Get one at: https://console.anthropic.com
```

### 3. Configure your document sources
```bash
# Open config.yaml and set your local folder paths
# Google Drive setup instructions are in GOOGLE_DRIVE_SETUP.md
```

### 4. Ingest your documents
```bash
python ingest.py
# First run may take a few minutes depending on doc count
# Re-run anytime you add new documents
```

### 5. Start asking questions
```bash
python query.py
```
*(The embedding model loads in the background so you can start typing instantly.)*

---

## Example Queries

```
You: What were the action items from the Q1 planning meeting?
You: Summarize the current status of Project Atlas
You: What did we decide about the pricing model in last month's MoM?
You: List all projects I'm currently working on
You: What are the risks mentioned across my project docs?
```

---

## Interactive Commands & Search Modes

While running `python query.py`, you can use the following commands in the prompt:
- `/sync` — Re-index documents without exiting.
- `/mode [hybrid|semantic|keyword]` — Switch between search modes (default: `hybrid`).
  - `hybrid`: Combines both conceptual and exact keyword matching.
  - `semantic`: Embedding-based search for conceptual queries.
  - `keyword`: Exact or partial word matching.
- `exit`, `quit`, `q`, `bye` — Close the assistant.

You can also pass arguments directly via CLI:
```bash
python query.py "Your question here"
python query.py --sync
python query.py --mode keyword
```

---

## File Structure

```
pm-rag/
├── ingest.py              # Index all your documents
├── query.py               # Interactive terminal chat
├── config.yaml            # Your storage paths & settings
├── requirements.txt       # Python dependencies
├── .env.example           # API key template
├── GOOGLE_DRIVE_SETUP.md  # Google Drive auth guide
└── chroma_db/             # Auto-created vector store (local)
```

---

## Supported File Types

| Format | Support |
|--------|---------|
| PDF | ✅ Full text extraction |
| Word (.docx) | ✅ Full text + tables |
| Excel (.xlsx) | ✅ All sheets |
| Markdown (.md) | ✅ Native |
| Google Docs | ✅ Via Drive API (exports as text) |
| Text (.txt) | ✅ Native |

---

## Re-indexing

Run `python ingest.py` (or type `/sync` in the interactive prompt) anytime you:
- Add new documents to your folders
- Update existing documents
- Add new Google Drive folders to config

The system uses content hashing — unchanged files are skipped automatically.

---

## Switching LLMs

In `config.yaml`, change the `llm` section:
- `anthropic` — Claude (default, best for long docs)
- `openai` — GPT-4o
- `gemini` — Google Gemini (gemini-2.5-flash, gemini-2.5-pro, etc.)
- `ollama` — Free local model (no API key needed)
