"""
检索 + RAG 问答
支持：自由问答 / 章节定向检索 / 多文档并行分析
"""
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL, TOP_K
from services.embedder import search, get_doc_sections

_client = None

# ── 章节关键词映射 ─────────────────────────────────────────────────────
_SECTION_KEYWORDS: list[tuple[str, str]] = [
    ('摘要',         'Abstract（摘要）'),
    ('关键词',       'Abstract（摘要）'),
    ('引言',         'Introduction（引言/简介）'),
    ('介绍',         'Introduction（引言/简介）'),
    ('背景',         'Background（背景）'),
    ('相关工作',     'Related Work（相关工作）'),
    ('文献综述',     'Literature Review（文献综述）'),
    ('研究方法',     'Methodology（研究方法）'),
    ('方法论',       'Methodology（研究方法）'),
    ('方法',         'Methods（方法）'),
    ('实验设置',     'Experimental Setup（实验设置）'),
    ('实验',         'Experiments（实验）'),
    ('评估',         'Evaluation（评估）'),
    ('结果',         'Results（结果）'),
    ('讨论',         'Discussion（讨论）'),
    ('性能评估',     'Performance Evaluation（性能评估）'),
    ('结论',         'Conclusion（结论）'),
    ('总结',         'Summary（总结）'),
    ('局限',         'Limitations（局限性）'),
    ('未来工作',     'Future Work（未来工作）'),
    ('abstract',     'Abstract（摘要）'),
    ('introduction', 'Introduction（引言/简介）'),
    ('background',   'Background（背景）'),
    ('related work', 'Related Work（相关工作）'),
    ('methodology',  'Methodology（研究方法）'),
    ('methods',      'Methods（方法）'),
    ('method',       'Methods（方法）'),
    ('experiments',  'Experiments（实验）'),
    ('evaluation',   'Evaluation（评估）'),
    ('results',      'Results（结果）'),
    ('discussion',   'Discussion（讨论）'),
    ('conclusion',   'Conclusion（结论）'),
    ('conclusions',  'Conclusions（结论）'),
    ('summary',      'Summary（总结）'),
    ('future work',  'Future Work（未来工作）'),
    ('limitations',  'Limitations（局限性）'),
]

# 多文档概览查询关键词
_MULTI_DOC_KEYWORDS = [
    '分析', '概述', '总结', '比较', '对比', '综述', '所有', '每个', '每篇',
    '各个', '四个', '三个', '两个', '多个', '这些', '所选',
    'analyze', 'compare', 'overview', 'summarize', 'all', 'each', 'these',
]

# 用于多文档分析时优先抓取的章节（最能代表论文核心的章节）
_OVERVIEW_SECTIONS = [
    'Abstract（摘要）',
    'Introduction（引言/简介）',
    'Conclusion（结论）',
    'Conclusions（结论）',
    'Summary（总结）',
    'Results（结果）',
    'Results and Discussion（结果与讨论）',
]


def _detect_section(question: str) -> str | None:
    q = question.lower()
    for kw, section in _SECTION_KEYWORDS:
        if kw in q:
            return section
    return None


def _is_multi_doc_overview(question: str, doc_ids: list | None) -> bool:
    """判断是否是多文档整体分析请求"""
    if not doc_ids or len(doc_ids) < 2:
        return False
    q = question.lower()
    return any(kw in q for kw in _MULTI_DOC_KEYWORDS)


# ── 检索函数 ───────────────────────────────────────────────────────────
def retrieve(
    query:   str,
    top_k:   int = TOP_K,
    doc_ids: list[str] | None = None,
    section: str | None = None,
) -> list[dict]:
    """单文档/全库语义检索，自动识别章节关键词"""
    if section is None:
        section = _detect_section(query)

    if section:
        hits = search(query, top_k=top_k, doc_ids=doc_ids, section=section)
        if len(hits) < top_k:
            extra = search(query, top_k=top_k, doc_ids=doc_ids)
            seen  = {h["chunk_id"] for h in hits}
            for h in extra:
                if h["chunk_id"] not in seen and len(hits) < top_k:
                    hits.append(h)
        return hits

    return search(query, top_k=top_k, doc_ids=doc_ids)


def retrieve_per_doc(query: str, doc_ids: list[str], chunks_per_doc: int = 4) -> dict[str, list[dict]]:
    """
    多文档分析：为每篇文档单独检索代表性内容。
    先尝试抓摘要/引言/结论等核心章节，再补充与问题最相关的块。
    """
    result = {}
    for doc_id in doc_ids:
        hits = []
        seen_chunks = set()

        # 第一步：优先抓核心章节（摘要、引言、结论）
        for sec in _OVERVIEW_SECTIONS:
            sec_hits = search(query, top_k=2, doc_ids=[doc_id], section=sec)
            for h in sec_hits:
                if h["chunk_id"] not in seen_chunks:
                    seen_chunks.add(h["chunk_id"])
                    hits.append(h)
            if len(hits) >= chunks_per_doc:
                break

        # 第二步：用查询语义补充（保证内容与问题相关）
        if len(hits) < chunks_per_doc:
            extra = search(query, top_k=chunks_per_doc * 2, doc_ids=[doc_id])
            for h in extra:
                if h["chunk_id"] not in seen_chunks and len(hits) < chunks_per_doc:
                    seen_chunks.add(h["chunk_id"])
                    hits.append(h)

        result[doc_id] = hits
    return result


def build_context(hits: list[dict]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        sec_label = f"【{h['section']}】" if h.get("section") else ""
        parts.append(
            f"[{i}] {sec_label} 来源：{h['filename']}（相关度 {h['score']:.0%}）\n{h['text']}"
        )
    return "\n\n---\n\n".join(parts)


def build_multi_doc_context(per_doc_hits: dict[str, list[dict]]) -> tuple[str, list[dict]]:
    """为多文档分析构建分文档上下文，返回 (context_str, sources)"""
    parts   = []
    sources = []
    for doc_id, hits in per_doc_hits.items():
        if not hits:
            continue
        filename = hits[0]["filename"]
        sources.append({"filename": filename, "score": max(h["score"] for h in hits), "section": ""})
        doc_parts = []
        for h in hits:
            sec = f"【{h['section']}】" if h.get("section") else ""
            doc_parts.append(f"{sec}\n{h['text']}")
        parts.append(f"═══ 文献：{filename} ═══\n" + "\n\n".join(doc_parts))

    return "\n\n\n".join(parts), sources


# ── RAG 问答主入口 ─────────────────────────────────────────────────────
def ask(
    question: str,
    stream:   bool = False,
    doc_ids:  list[str] | None = None,
    section:  str | None = None,
):
    # ── 模式判断 ──
    is_multi = _is_multi_doc_overview(question, doc_ids)

    if is_multi:
        # 多文档并行分析模式
        per_doc = retrieve_per_doc(question, doc_ids, chunks_per_doc=4)
        context, sources = build_multi_doc_context(per_doc)
        doc_count = len([v for v in per_doc.values() if v])
        prompt = f"""你是专业学术文献分析助手。用户选择了 {doc_count} 篇文献，请逐篇分析，给出结构化报告。

要求：
- 每篇文献单独一节，用"## 文献X：[文件名]"作为标题
- 每篇包含：研究主题、核心方法、主要结论（如内容不足可说明）
- 最后加一节"## 综合对比"，比较各篇的异同或关联
- 全程使用中文，条理清晰

参考内容（已按文献分组）：
{context}

用户请求：{question}"""

    else:
        # 常规单文档/全库检索模式
        hits    = retrieve(question, doc_ids=doc_ids, section=section)
        context = build_context(hits)
        sources = [
            {"filename": h["filename"], "score": h["score"], "section": h.get("section", "")}
            for h in hits
        ]
        task_hint = f'用户当前正在阅读【{section}】章节，请围绕该章节内容作答。' if section \
                    else '请根据参考文档内容回答用户问题。'
        prompt = f"""你是专业学术文献助手。{task_hint}

规则：
- 只使用下方参考文档中的内容，不引入外部知识。
- 默认用中文回答；用户若用英文提问可用英文回答。
- 回答结构清晰，引用信息时注明来源章节。
- 若文档中没有相关内容，明确说明"提供的文档中未找到该信息"。

参考文档：
{context}

用户问题：{question}"""

    # ── 调用 LLM ──
    client = _get_client()

    if not stream:
        resp = client.chat.completions.create(
            model=LLM_MODEL, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content, sources

    def _stream_gen(p=prompt, s=sources):
        resp = client.chat.completions.create(
            model=LLM_MODEL, max_tokens=4096,
            messages=[{"role": "user", "content": p}],
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        yield {"sources": s}

    return _stream_gen()


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client
