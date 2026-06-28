"""
向量化服务：Jina AI Embedding API + Supabase pgvector
持久化存储，Railway 重启/重部署后数据不丢失
"""
import os, re, time
import requests
import psycopg2
import psycopg2.extras
from config import JINA_API_KEY

DATABASE_URL   = os.getenv("DATABASE_URL", "")
_JINA_MAX_CHARS = 8000
_META_PREFIX_RE = re.compile(r'^\[文档:[^\]]*\](?:\[章节:[^\]]*\])?\s*\n?')
_table_ready    = False


# ── DB 连接 ──────────────────────────────────────────────────────────────
def _conn():
    return psycopg2.connect(DATABASE_URL)


def _ensure_table():
    """首次调用时建表建索引（幂等）。"""
    global _table_ready
    if _table_ready:
        return
    conn = _conn()
    try:
        cur = conn.cursor()
        # pgvector 扩展（Supabase 默认已启用；失败则忽略）
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()
        except Exception:
            conn.rollback()
        # 主表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS doc_chunks (
                id               TEXT PRIMARY KEY,
                doc_id           TEXT NOT NULL,
                filename         TEXT NOT NULL,
                chunk_id         INTEGER NOT NULL,
                text             TEXT NOT NULL,
                lang             TEXT DEFAULT 'en',
                section          TEXT DEFAULT '',
                parent_group_id  TEXT DEFAULT '',
                child_seq        INTEGER DEFAULT 0,
                embedding        vector(768)
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_doc_id ON doc_chunks(doc_id);"
        )
        # HNSW 向量索引（可选；无索引时自动顺序扫描）
        try:
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dc_emb
                ON doc_chunks USING hnsw (embedding vector_cosine_ops);
            """)
        except Exception:
            pass
        conn.commit()
        cur.close()
        _table_ready = True
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"pgvector 初始化失败: {e}")
    finally:
        conn.close()


# ── Jina Embedding ───────────────────────────────────────────────────────
def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    task       = "retrieval.query" if is_query else "retrieval.passage"
    safe_texts = [t[:_JINA_MAX_CHARS] for t in texts]
    last_err   = None
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
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Jina 向量化失败（已重试3次）: {last_err}")


def _vec(emb: list[float]) -> str:
    return '[' + ','.join(f'{v:.8f}' for v in emb) + ']'


# ── CRUD ─────────────────────────────────────────────────────────────────
def add_chunks(chunks: list[dict], batch_size: int = 16) -> int:
    _ensure_table()
    added = 0
    for i in range(0, len(chunks), batch_size):
        batch      = chunks[i:i + batch_size]
        embeddings = embed_texts([c["text"] for c in batch], is_query=False)
        conn = _conn()
        try:
            cur = conn.cursor()
            for c, emb in zip(batch, embeddings):
                cur.execute("""
                    INSERT INTO doc_chunks
                        (id, doc_id, filename, chunk_id, text, lang, section,
                         parent_group_id, child_seq, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    c["id"], c["doc_id"], c["filename"], c["chunk_id"],
                    c["text"], c.get("lang", "en"), c.get("section", ""),
                    c.get("parent_group_id", ""), c.get("child_seq", 0),
                    _vec(emb),
                ))
            conn.commit()
            cur.close()
            added += len(batch)
            print(f"向量化入库: {i + len(batch)}/{len(chunks)}")
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    return added


def delete_doc(doc_id: str) -> int:
    _ensure_table()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id=%s", (doc_id,))
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM doc_chunks WHERE doc_id=%s", (doc_id,))
        conn.commit()
        cur.close()
        return count
    finally:
        conn.close()


def collection_count() -> int:
    _ensure_table()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM doc_chunks")
        n = cur.fetchone()[0]
        cur.close()
        return n
    finally:
        conn.close()


def get_doc_header(doc_id: str, n: int = 3) -> list[dict]:
    _ensure_table()
    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT text, filename, doc_id, section
            FROM doc_chunks WHERE doc_id=%s ORDER BY chunk_id LIMIT %s
        """, (doc_id, n))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_doc_sections(doc_id: str) -> list[str]:
    _ensure_table()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT section, MIN(chunk_id) AS first_chunk
            FROM doc_chunks WHERE doc_id=%s AND section!=''
            GROUP BY section ORDER BY first_chunk
        """, (doc_id,))
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]
    finally:
        conn.close()


def search(query: str, top_k: int = 8, doc_ids: list[str] | None = None,
           section: str | None = None) -> list[dict]:
    _ensure_table()
    total = collection_count()
    if total == 0:
        return []

    query_vec = embed_texts([query], is_query=True)[0]
    vec       = _vec(query_vec)

    where_parts, where_params = [], []
    if doc_ids:
        if len(doc_ids) == 1:
            where_parts.append("doc_id = %s")
            where_params.append(doc_ids[0])
        else:
            where_parts.append("doc_id = ANY(%s)")
            where_params.append(doc_ids)
    if section:
        where_parts.append("section = %s")
        where_params.append(section)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = f"""
        SELECT id, doc_id, filename, chunk_id, text, section, parent_group_id,
               1 - (embedding <=> %s::vector) AS score
        FROM doc_chunks
        {where_clause}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params = [vec] + where_params + [vec, min(top_k, total)]

    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [{
            "text":            r["text"],
            "filename":        r["filename"],
            "doc_id":          r["doc_id"],
            "chunk_id":        r["chunk_id"],
            "section":         r["section"],
            "parent_group_id": r["parent_group_id"],
            "score":           round(float(r["score"]), 4),
        } for r in rows]
    finally:
        conn.close()


def expand_hits_to_parent(hits: list[dict], window: int = 1) -> list[dict]:
    if not hits:
        return hits
    _ensure_table()
    expanded = []
    for hit in hits:
        doc_id = hit["doc_id"]
        cid    = int(hit.get("chunk_id", 0))
        conn   = _conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT text, chunk_id FROM doc_chunks
                WHERE doc_id=%s AND chunk_id BETWEEN %s AND %s
                ORDER BY chunk_id
            """, (doc_id, max(0, cid - window), cid + window))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                expanded.append(hit)
                continue
            texts = []
            for idx, row in enumerate(rows):
                t = row["text"]
                texts.append(t if idx == 0 else _META_PREFIX_RE.sub('', t))
            expanded.append({**hit, "text": "\n\n".join(texts)})
        except Exception:
            expanded.append(hit)
        finally:
            conn.close()
    return expanded
