"""
向量化服务：Jina AI Embedding API + Chroma
无需本地模型，零内存占用
"""
import requests
import chromadb
from tqdm import tqdm
from config import CHROMA_DIR, COLLECTION_NAME, JINA_API_KEY

_collection = None

def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    task = "retrieval.query" if is_query else "retrieval.passage"
    resp = requests.post(
        "https://api.jina.ai/v1/embeddings",
        headers={"Authorization": f"Bearer {JINA_API_KEY}", "Content-Type": "application/json"},
        json={"model": "jina-embeddings-v3", "input": texts, "task": task, "dimensions": 768},
        timeout=30,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client      = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name     = COLLECTION_NAME,
            metadata = {"hnsw:space": "cosine"},
        )
    return _collection


def add_chunks(chunks: list[dict], batch_size: int = 32) -> int:
    collection = _get_collection()
    added = 0
    for i in tqdm(range(0, len(chunks), batch_size), desc="向量化入库"):
        batch      = chunks[i:i + batch_size]
        texts      = [c["text"] for c in batch]
        embeddings = embed_texts(texts, is_query=False)
        collection.add(
            ids        = [c["id"] for c in batch],
            documents  = texts,
            embeddings = embeddings,
            metadatas  = [{
                "doc_id":   c["doc_id"],
                "filename": c["filename"],
                "chunk_id": c["chunk_id"],
                "lang":     c.get("lang", "en"),
                "section":  c.get("section", ""),
            } for c in batch],
        )
        added += len(batch)
    return added


def delete_doc(doc_id: str) -> int:
    collection = _get_collection()
    results    = collection.get(where={"doc_id": doc_id})
    ids        = results.get("ids", [])
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def collection_count() -> int:
    return _get_collection().count()


def get_doc_header(doc_id: str, n: int = 3) -> list[dict]:
    """返回文档最前面的 n 个 chunk（含作者/标题/年份/期刊信息）"""
    collection = _get_collection()
    results    = collection.get(where={"doc_id": doc_id},
                                include=["documents", "metadatas"])
    if not results["ids"]:
        return []
    items = sorted(
        zip(results["documents"], results["metadatas"]),
        key=lambda x: int(x[1].get("chunk_id", 0))
    )
    return [{"text": t, "filename": m["filename"], "doc_id": m["doc_id"],
             "section": m.get("section", "")} for t, m in items[:n]]


def get_doc_sections(doc_id: str) -> list[str]:
    collection = _get_collection()
    results    = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
    seen, ordered = set(), []
    for m in results["metadatas"]:
        sec = m.get("section", "").strip()
        if sec and sec not in seen:
            seen.add(sec); ordered.append(sec)
    return ordered


def search(query: str, top_k: int = 8, doc_ids: list[str] | None = None,
           section: str | None = None) -> list[dict]:
    collection = _get_collection()
    query_vec  = embed_texts([query], is_query=True)

    conditions = []
    if doc_ids:
        conditions.append({"doc_id": {"$eq": doc_ids[0]}} if len(doc_ids) == 1
                          else {"doc_id": {"$in": doc_ids}})
    if section:
        conditions.append({"section": {"$eq": section}})

    where = None if not conditions else (
        conditions[0] if len(conditions) == 1 else {"$and": conditions}
    )

    total = collection.count()
    if total == 0:
        return []

    kwargs = dict(query_embeddings=query_vec, n_results=min(top_k, total),
                  include=["documents", "metadatas", "distances"])
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)
    return [{
        "text":     results["documents"][0][i],
        "filename": results["metadatas"][0][i]["filename"],
        "doc_id":   results["metadatas"][0][i]["doc_id"],
        "chunk_id": results["metadatas"][0][i]["chunk_id"],
        "section":  results["metadatas"][0][i].get("section", ""),
        "score":    round(1 - results["distances"][0][i], 4),
    } for i in range(len(results["documents"][0]))]
