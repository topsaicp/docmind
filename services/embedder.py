"""
向量化服务：硅基流动 BGE-M3 Embedding API + 本机 pgvector
（由 Jina jina-embeddings-v3/768维 迁移而来，维度变更为 1024）
"""
import os, re, time
import requests
import psycopg2
import psycopg2.extras

DATABASE_URL   = os.getenv("DATABASE_URL", "")

# ── 硅基流动配置 ─────────────────────────────────────────────────────────
# 环境变量新增 SILICONFLOW_API_KEY（siliconflow.cn 控制台生成）
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
_EMBED_API_URL   = "https://api.siliconflow.cn/v1/embeddings"
_EMBED_MODEL     = "BAAI/bge-m3"     # 1024 维，中英双语检索强项
_EMBED_DIM       = 1024
_EMBED_MAX_CHARS = 6000              # bge-m3 上限 8192 token，中文按 1字≈1token 留余量
_EMBED_BATCH     = 16                # 单请求批量条数

_META_PREFIX_RE = re.compile(r'^\[文档:[^\]]*\](?:\[章节:[^\]]*\])?\s*\n?')

def _clean(s):
    return s.replace('\x00', '') if isinstance(s, str) else s



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
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()
        except Exception:
            conn.rollback()
        cur.execute(f"""
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
                embedding        vector({_EMBED_DIM})
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_doc_id ON doc_chunks(doc_id);"
        )
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


# ── 硅基流动 Embedding（OpenAI 兼容接口）────────────────────────────────
def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    """BGE-M3 检索场景下 query 与 passage 同一编码方式，is_query 参数保留兼容签名。"""
    safe_texts = [t[:_EMBED_MAX_CHARS] for t in texts]
    results: list[list[float]] = []
    # 分批请求，避免单请求过大
    for i in range(0, len(safe_texts), _EMBED_BATCH):
        batch    = safe_texts[i:i + _EMBED_BATCH]
        last_err = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    _EMBED_API_URL,
                    headers={"Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": _EMBED_MODEL, "input": batch},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                # 按 index 排序保证顺序与输入一致
                data.sort(key=lambda x: x["index"])
                results.extend(item["embedding"] for item in data)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(2 ** attempt)
        if last_err is not None:
            raise RuntimeError(f"硅基流动向量化失败（已重试3次）: {last_err}")
    return results


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
                    c["id"], c["doc_id"], _clean(c["filename"]), c["chunk_id"],
                    _clean(c["text"]), c.get("lang", "en"), _clean(c.get("section", "")),
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
