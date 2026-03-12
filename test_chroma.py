import chromadb

client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_collection(name="pm_docs")

collection.upsert(
    ids=["test_meta_doc"],
    documents=["This is a MiXeD CaSe document."],
    metadatas=[{"content_lower": "this is a mixed case document."}]
)

res = collection.get(
    where={"content_lower": {"$contains": "mixed"}},
    include=["documents"]
)
print(f"Matched by metadata: {len(res['documents'])}")

collection.delete(ids=["test_meta_doc"])
