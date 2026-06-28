"""
向量化服务：Jina AI Embedding API + Chroma
无需本地模型，零内存占用

v2 新增：
- add_chunks() 存储 parent_group_id / child_seq 元数据
- expand_hits_to_parent() 将检索命中的子块展开为父块上下文
"""
import re
import requests
import chromadb
from tqdm import tqdm
from config import CHROMA_DIR, COLLECTION_NAME, JINA_API_KEY

_collection = None

# 元数据前缀模式（用于展开时剥离重复前缀）
_META_PREFIX_RE = re.compile(r'^\[文档:[^\]]*\](?:\[章节:[^\]]*\])?\s*\n?')


_JINA_MAX_CHARS = 8000   # jina-embeddings-v3 单条输入上限

def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    task = "retrieval.query" if is_query else "retrieval.passage"
    # 超长文本截断，防止 Jina 拒绝请求
    safe_texts = [t[:_JINA_MAX_CHARS] for t in texts]

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.jina.ai/v1/embeddings",
                headers={"Authorization": f"Bearer {JINA_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "jina-embeddings-v3", "input": safe_texts,
                      "task": task, "dimensions": 768},
                timeout=60,
            )
            resp.raise_for_status()
            return [item["embedding"] for item in resp.json()["data"]]
        except Exception as e:
            last_err = e
            import time; time.sleep(2 ** attempt)   # 1s / 2s / 4s 退避
    raise RuntimeError(f"Jina 向量化失败（已重试3次）: {last_err}")


def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client      = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name     = COLLECTION_NAME,
            metadata = {"hnsw:space": "cosine"},
        )
    return _collection


def add_chunks(chunks: list[dict], batch_size: int = 16) -> int:
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
                "doc_id":          c["doc_id"],
                "filename":        c["filename"],
                "chunk_id":        c["chunk_id"],
                "lang":            c.get("lang", "en"),
                "section":         c.get("section", ""),
                # 父子块追踪字段（v2）
                "parent_group_id": c.get("parent_group_id", ""),
                "child_seq":       c.get("child_seq", 0),
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
        "text":            results["documents"][0][i],
        "filename":        results["metadatas"][0][i]["filename"],
        "doc_id":          results["metadatas"][0][i]["doc_id"],
        "chunk_id":        results["metadatas"][0][i]["chunk_id"],
        "section":         results["metadatas"][0][i].get("section", ""),
        "parent_group_id": results["metadatas"][0][i].get("parent_group_id", ""),
        "score":           round(1 - results["distances"][0][i], 4),
    } for i in range(len(results["documents"][0]))]


def expand_hits_to_parent(hits: list[dict], window: int = 1) -> list[dict]:
    """
    父块展开：将每个子块命中扩展为「前 window 块 + 命中块 + 后 window 块」的
    父块上下文，为 LLM 提供更完整的原文语境。

    - window=1 → 父块 = 3 个子块（约 840 中文字 / 1440 英文字）
    - 只展开同一文档内的相邻块，不跨文档
    - 相邻块的元数据前缀 [文档: ...][章节: ...] 仅保留首块，避免重复
    - 对旧版文档（无 parent_group_id 元数据）降级：直接按 chunk_id ±window 取块
    """
    if not hits:
        return hits

    collection = _get_collection()
    expanded   = []

    for hit in hits:
        doc_id = hit["doc_id"]
        cid    = int(hit.get("chunk_id", 0))
        ids    = [f"{doc_id}_{i}" for i in range(max(0, cid - window), cid + window + 1)]

        try:
            res = collection.get(ids=ids, include=["documents", "metadatas"])
            if not res["documents"]:
                expanded.append(hit)
                continue

            # 按 chunk_id 排序
            pairs = sorted(
                zip(res["documents"], res["metadatas"]),
                key=lambda x: int(x[1].get("chunk_id", 0)),
            )

            # 首块保留前缀，其余剥除（避免重复的元数据前缀干扰 LLM）
            texts = []
            for idx, (doc_text, _) in enumerate(pairs):
                if idx == 0:
                    texts.append(doc_text)
                else:
                    texts.append(_META_PREFIX_RE.sub('', doc_text))

            expanded.append({**hit, "text": "\n\n".join(texts)})

        except Exception:
            expanded.append(hit)   # 任何异常降级为原始子块

    return expanded
