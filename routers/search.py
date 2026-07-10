"""
文献检索接口
POST /api/search/literature  → AI 提取关键词 → Semantic Scholar + CrossRef → APA 格式
"""
import re, json
import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI

from db.database import User
from routers.auth import get_current_user
from config import MODEL_ROUTES

router = APIRouter(prefix="/api/search", tags=["search"])

_SS_URL  = "https://api.semanticscholar.org/graph/v1/paper/search"
_CR_URL  = "https://api.crossref.org/works"
_HEADERS = {"User-Agent": "DocMind/1.0 (mailto:topsai@protonmail.com)"}


class SearchReq(BaseModel):
    topic: str
    limit: int = 10   # 返回总条数上限


# ── 关键词提取 ────────────────────────────────────────────────────────────

def _extract_keywords(topic: str) -> list[str]:
    key, base, model = MODEL_ROUTES["cite"]
    client = OpenAI(api_key=key, base_url=base)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": (
            f"用户研究主题：{topic}\n\n"
            "请提取并扩展成 4~6 个英文学术检索词（覆盖核心概念及相关术语），"
            "只返回 JSON 字符串数组，不要其他内容。\n"
            '示例：["machine learning", "neural network", "deep learning", "gradient descent"]'
        )}],
        temperature=0.2,
        max_tokens=200,
    )
    raw = resp.choices[0].message.content.strip()
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    # fallback：按逗号分割
    return [w.strip().strip('"') for w in raw.strip('[]').split(',') if w.strip()]


# ── 数据库检索 ─────────────────────────────────────────────────────────────

def _semantic_scholar(query: str, limit: int) -> list[dict]:
    try:
        r = requests.get(_SS_URL, params={
            "query": query,
            "fields": "title,authors,year,abstract,externalIds,venue,publicationVenue,citationCount",
            "limit": limit,
        }, headers=_HEADERS, timeout=12)
        return r.json().get("data", []) if r.ok else []
    except Exception:
        return []


def _crossref(query: str, limit: int) -> list[dict]:
    try:
        r = requests.get(_CR_URL, params={
            "query": query,
            "rows": limit,
            "select": "title,author,published,DOI,container-title,volume,issue,page,type",
        }, headers={**_HEADERS, "mailto": "topsai@protonmail.com"}, timeout=12)
        return r.json().get("message", {}).get("items", []) if r.ok else []
    except Exception:
        return []


# ── APA 格式化 ─────────────────────────────────────────────────────────────

def _fmt_authors_apa(names: list[str]) -> str:
    """names 形如 ['Smith, J.', 'Lee, A.'] 或普通姓名"""
    if not names:
        return "Unknown Author"
    if len(names) == 1:
        return names[0]
    if len(names) <= 7:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:6]) + ", . . . " + names[-1]


def _name_to_apa(full: str) -> str:
    """'FirstName LastName' → 'LastName, F.'"""
    parts = full.strip().split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {'. '.join(p[0] for p in parts[:-1])}."
    return full


def _ss_to_result(item: dict) -> dict:
    authors_raw = [a.get("name", "") for a in item.get("authors", [])]
    authors_apa = [_name_to_apa(n) for n in authors_raw]
    author_str  = _fmt_authors_apa(authors_apa)
    year  = item.get("year") or "n.d."
    title = (item.get("title") or "").rstrip(".")
    venue = (item.get("publicationVenue") or {}).get("name") or item.get("venue") or ""
    doi   = (item.get("externalIds") or {}).get("DOI", "")

    apa = f"{author_str} ({year}). {title}."
    if venue:
        apa += f" *{venue}*."
    if doi:
        apa += f" https://doi.org/{doi}"

    return {
        "title": title, "authors": authors_raw, "year": str(year),
        "venue": venue, "doi": doi,
        "abstract": (item.get("abstract") or "")[:400],
        "citations": item.get("citationCount"),
        "apa": apa, "source": "Semantic Scholar",
        "url": f"https://doi.org/{doi}" if doi else "",
    }


def _cr_to_result(item: dict) -> dict:
    authors_raw = []
    authors_apa = []
    for a in item.get("author", []):
        family = a.get("family", "")
        given  = a.get("given", "")
        full   = f"{given} {family}".strip() if given else family
        authors_raw.append(full)
        apa_name = f"{family}, {'. '.join(p[0] for p in given.split())}." if given else family
        authors_apa.append(apa_name)

    author_str = _fmt_authors_apa(authors_apa)

    dp   = (item.get("published") or {}).get("date-parts", [[]])[0]
    year = dp[0] if dp else "n.d."

    title_list = item.get("title") or [""]
    title = (title_list[0] if title_list else "").rstrip(".")
    journal_list = item.get("container-title") or []
    journal = journal_list[0] if journal_list else ""
    volume  = item.get("volume", "")
    issue   = item.get("issue", "")
    page    = item.get("page", "")
    doi     = item.get("DOI", "")

    apa = f"{author_str} ({year}). {title}."
    if journal:
        vi = f"{volume}({issue})" if volume and issue else (volume or "")
        pg = f", {page}" if page else ""
        apa += f" *{journal}*" + (f", {vi}{pg}." if vi else ".")
    if doi:
        apa += f" https://doi.org/{doi}"

    return {
        "title": title, "authors": authors_raw, "year": str(year),
        "venue": journal, "doi": doi, "abstract": "",
        "citations": None,
        "apa": apa, "source": "CrossRef",
        "url": f"https://doi.org/{doi}" if doi else "",
    }


# ── 接口 ──────────────────────────────────────────────────────────────────

@router.post("/literature")
def search_literature(req: SearchReq, current_user: User = Depends(get_current_user)):
    if not current_user.email_verified:
        raise HTTPException(403, "请先验证邮箱")
    if not req.topic.strip():
        raise HTTPException(400, "请输入研究主题")

    # Step 1: AI 提取关键词
    keywords = _extract_keywords(req.topic)
    query    = " ".join(keywords[:4])

    # Step 2: 并行检索（各取一半配额，去重后合并）
    half   = max(req.limit // 2, 4)
    ss_raw = _semantic_scholar(query, limit=half + 2)
    cr_raw = _crossref(query, limit=half)

    # Step 3: 格式化 + 去重（按 DOI）
    results  = []
    seen_doi = set()

    for item in ss_raw:
        r = _ss_to_result(item)
        if r["doi"] and r["doi"] in seen_doi:
            continue
        if r["doi"]:
            seen_doi.add(r["doi"])
        if r["title"]:
            results.append(r)

    for item in cr_raw:
        r = _cr_to_result(item)
        if r["doi"] and r["doi"] in seen_doi:
            continue
        if r["doi"]:
            seen_doi.add(r["doi"])
        if r["title"]:
            results.append(r)

    # 按引用数降序（Semantic Scholar 有，CrossRef 没有）
    results.sort(key=lambda x: x["citations"] or 0, reverse=True)

    return {
        "topic":    req.topic,
        "keywords": keywords,
        "query":    query,
        "total":    len(results),
        "results":  results[:req.limit],
    }
