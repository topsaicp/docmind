"""
向量化服务：BCEmbedding + Chroma
支持入库、删除、按文档/章节检索
"""
import chromadb
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from config import CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL

_model      = None
_collection = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"正在加载 Embedding 模型 {EMBED_MODEL}（首次约需1-2分钟下载）...")
        _model = SentenceTransformer(EMBED_MODEL)
        print("✅ 模型加载完成")
    return _model


def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client      = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name     = COLLECTION_NAME,
            metadata = {"hnsw:space": "cosine"},
        )
    return _collection


def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    model = _get_model()
    vecs  = model.encode(
        texts,
        normalize_embeddings = True,
        batch_size           = 32,
        show_progress_bar    = False,
    )
    return vecs.tolist()


def add_chunks(chunks: list[dict], batch_size: int = 64) -> int:
    collection = _get_collection()
    added      = 0
    for i in tqdm(range(0, len(chunks), batch_size), desc="向量化入库"):
        batch      = chunks[i:i + batch_size]
        texts      = [c["text"] for c in batch]
        embeddings = embed_texts(texts, is_query=False)
        collection.add(
            ids        = [c["id"] for c in batch],
            documents  = texts,
            embeddings = embeddings,
            metadatas  = [
                {
                    "doc_id":   c["doc_id"],
                    "filename": c["filename"],
                    "chunk_id": c["chunk_id"],
                    "lang":     c.get("lang", "en"),
                    "section":  c.get("section", ""),
                }
                for c in batch
            ],
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


def get_doc_sections(doc_id: str) -> list[str]:
    """返回某篇文档包含的章节名列表（有序去重）"""
    collection = _get_collection()
    results    = collection.get(
        where   = {"doc_id": doc_id},
        include = ["metadatas"],
    )
    seen    = set()
    ordered = []
    for m in results["metadatas"]:
        sec = m.get("section", "").strip()
        if sec and sec not in seen:
            seen.add(sec)
            ordered.append(sec)
    return ordered


def search(
    query:   str,
    top_k:   int = 8,
    doc_ids: list[str] | None = None,
    section: str | None = None,
) -> list[dict]:
    """
    语义检索。
    doc_ids 非空 → 只在指定文档内搜索
    section 非空  → 只在该章节内搜索
    """
    collection = _get_collection()
    query_vec  = embed_texts([query], is_query=True)

    # 构建 where 过滤条件
    conditions = []
    if doc_ids:
        if len(doc_ids) == 1:
            conditions.append({"doc_id": {"$eq": doc_ids[0]}})
        else:
            conditions.append({"doc_id": {"$in": doc_ids}})
    if section:
        conditions.append({"section": {"$eq": section}})

    if len(conditions) == 0:
        where = None
    elif len(conditions) == 1:
        where = conditions[0]
    else:
        where = {"$and": conditions}

    total = collection.count()
    if total == 0:
        return []

    kwargs = dict(
        query_embeddings = query_vec,
        n_results        = min(top_k, total),
        include          = ["documents", "metadatas", "distances"],
    )
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    hits = []
    for i in range(len(results["documents"][0])):
        hits.append({
            "text":     results["documents"][0][i],
            "filename": results["metadatas"][0][i]["filename"],
            "doc_id":   results["metadatas"][0][i]["doc_id"],
            "chunk_id": results["metadatas"][0][i]["chunk_id"],
            "section":  results["metadatas"][0][i].get("section", ""),
            "score":    round(1 - results["distances"][0][i], 4),
        })
    return hits
