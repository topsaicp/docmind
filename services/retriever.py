"""
检索 + RAG 问答
支持：自由问答 / 章节定向检索 / 多文档并行分析
"""
from openai import OpenAI
from config import MODEL_ROUTES, TOP_K
from services.embedder import search, get_doc_sections, get_doc_header, expand_hits_to_parent

# 每个任务类型独立缓存一个 OpenAI-compatible client
_clients: dict[str, OpenAI] = {}


def _router(task: str) -> tuple[OpenAI, str]:
    """
    根据任务类型返回 (client, model_id)。
    路由表在 config.MODEL_ROUTES 中集中维护：
      "qa"      → 普通问答
      "multi"   → 多文档对比
      "review"  → 文献综述
      "writing" → 论文撰写
      "cite"    → 引用元数据提取
    未知任务降级到 "qa"。
    """
    key, base_url, model_id = MODEL_ROUTES.get(task, MODEL_ROUTES["qa"])
    if task not in _clients:
        _clients[task] = OpenAI(api_key=key, base_url=base_url)
    return _clients[task], model_id

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

# 综述请求关键词
_REVIEW_KEYWORDS = [
    '综述', '文献综述', '研究综述', '写综述', '撰写综述', '综合分析',
    '写一篇', '生成综述', '帮我写', 'literature review', 'write a review',
]

# 噪音章节（检索时排除）
_NOISE_SECTIONS = {
    'References（参考文献）', 'Acknowledgements（致谢）', 'Acknowledgments（致谢）',
    'Appendix（附录）', 'Bibliography（书目）',
}

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
    if not doc_ids or len(doc_ids) < 2:
        return False
    q = question.lower()
    return any(kw in q for kw in _MULTI_DOC_KEYWORDS)


def _is_review_request(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _REVIEW_KEYWORDS)


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
    """多文档分析：每篇单独检索，优先核心章节，过滤噪音章节。"""
    result = {}
    for doc_id in doc_ids:
        hits = []
        seen_chunks = set()

        for sec in _OVERVIEW_SECTIONS:
            sec_hits = search(query, top_k=2, doc_ids=[doc_id], section=sec)
            for h in sec_hits:
                if h["chunk_id"] not in seen_chunks and h.get("section","") not in _NOISE_SECTIONS:
                    seen_chunks.add(h["chunk_id"])
                    hits.append(h)
            if len(hits) >= chunks_per_doc:
                break

        if len(hits) < chunks_per_doc:
            extra = search(query, top_k=chunks_per_doc * 2, doc_ids=[doc_id])
            for h in extra:
                if h["chunk_id"] not in seen_chunks \
                        and h.get("section","") not in _NOISE_SECTIONS \
                        and len(hits) < chunks_per_doc:
                    seen_chunks.add(h["chunk_id"])
                    hits.append(h)

        result[doc_id] = hits
    return result


def retrieve_per_doc_for_review(query: str, doc_ids: list[str],
                                chunks_per_doc: int = 12) -> dict[str, list[dict]]:
    """综述模式：每篇抽取更多块，覆盖摘要/引言/方法/结果/结论各节。"""
    REVIEW_SECTIONS = [
        'Abstract（摘要）',
        'Introduction（引言/简介）',
        'Methodology（研究方法）', 'Methods（方法）',
        'Results（结果）', 'Results and Discussion（结果与讨论）',
        'Discussion（讨论）',
        'Conclusion（结论）', 'Conclusions（结论）',
        'Related Work（相关工作）',
    ]
    result = {}
    for doc_id in doc_ids:
        hits = []
        seen_chunks = set()

        # 每个核心章节各抓 2 块，保证章节覆盖均衡
        for sec in REVIEW_SECTIONS:
            sec_hits = search(query, top_k=2, doc_ids=[doc_id], section=sec)
            for h in sec_hits:
                if h["chunk_id"] not in seen_chunks and h.get("section","") not in _NOISE_SECTIONS:
                    seen_chunks.add(h["chunk_id"])
                    hits.append(h)

        # 补充语义相关块到 chunks_per_doc
        if len(hits) < chunks_per_doc:
            extra = search(query, top_k=chunks_per_doc * 2, doc_ids=[doc_id])
            for h in extra:
                if h["chunk_id"] not in seen_chunks \
                        and h.get("section","") not in _NOISE_SECTIONS \
                        and len(hits) < chunks_per_doc:
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
    question:  str,
    stream:    bool = False,
    doc_ids:   list[str] | None = None,
    section:   str | None = None,
    task_hint: str = "",          # 前端显式传入任务类型，优先于关键词自动检测
):
    # ── 任务类型解析（显式 task_hint 优先；否则关键词自动检测）──
    if task_hint in ("qa", "multi", "review", "writing", "cite"):
        task = task_hint
        # task_hint 为 "review"/"writing" 时仍走对应的检索路径
        is_review = task in ("review",) and doc_ids and len(doc_ids) >= 1
        is_multi  = task == "multi" and doc_ids and len(doc_ids) >= 2
    else:
        is_review = _is_review_request(question) and doc_ids and len(doc_ids) >= 1
        is_multi  = _is_multi_doc_overview(question, doc_ids) and not is_review
        task = "review" if is_review else ("multi" if is_multi else "qa")

    # ── 获取当前任务对应的 LLM 客户端和模型 ──
    client, model_id = _router(task)

    # ── Prompt 构建 ───────────────────────────────────────────────────
    if is_review:
        target_ids = doc_ids if doc_ids else []
        per_doc    = retrieve_per_doc_for_review(question, target_ids, chunks_per_doc=12)
        context, sources = build_multi_doc_context(per_doc)
        doc_count  = len([v for v in per_doc.values() if v])

        header_parts = []
        for idx, doc_id in enumerate(target_ids, 1):
            headers = get_doc_header(doc_id, n=3)
            if headers:
                header_text = "\n".join(h["text"] for h in headers)
                header_parts.append(f"[{idx}] 文件：{headers[0]['filename']}\n{header_text}")
        headers_block = "\n\n---\n\n".join(header_parts)

        prompt = f"""你是一位资深学术写作专家。请基于以下 {doc_count} 篇文献的内容，撰写一篇规范的学术文献综述。

【正文格式】严格按以下结构：
一、引言（研究背景与意义，综述主题范围）
二、研究现状（按主题横向分段，整合多篇文献观点，不要逐篇描述）
三、对比分析（方法、数据、结论的异同，客观评价优缺点）
四、不足与展望（现有局限性，未来研究方向）
五、结语（简洁总结核心发现）

【行文规范】
- 使用学术书面语，段落之间有过渡句，禁止项目符号列表
- 正文引用观点时标注编号，如（[1]）（[2][3]）
- 字数不少于1200字

【参考文献】
- 正文结束后，另起一行写"参考文献"作为标题
- 按 APA 第7版格式逐条列出，编号与正文引用编号对应

══════════════════════════════════════
各篇论文首页信息（用于提取APA参考文献）：
{headers_block}

══════════════════════════════════════
各篇论文正文内容（用于撰写综述）：
{context}

用户要求：{question}"""

    elif is_multi:
        per_doc = retrieve_per_doc(question, doc_ids, chunks_per_doc=4)
        context, sources = build_multi_doc_context(per_doc)
        doc_count = len([v for v in per_doc.values() if v])
        prompt = f"""你是专业学术文献分析助手。用户选择了 {doc_count} 篇文献，请逐篇分析后给出综合报告。

要求：
- 每篇文献单独一节，用"## 文献X：[文件名]"作为标题
- 每篇包含：研究主题、核心方法、主要结论
- 最后加"## 综合对比"一节，横向比较各篇的异同与关联
- 全程使用中文，语言简洁准确

参考内容（已按文献分组）：
{context}

用户请求：{question}"""

    elif task == "writing":
        # 论文撰写：前端已构造好结构化 Prompt，直接检索并生成
        hits    = retrieve(question, doc_ids=doc_ids, section=section)
        hits    = expand_hits_to_parent(hits, window=1)
        context = build_context(hits)
        sources = [{"filename": h["filename"], "score": h["score"],
                    "section": h.get("section", "")} for h in hits]
        prompt = f"""你是专业学术写作助手，请严格按照指示完成写作任务。

参考文献内容：
{context}

写作指令：{question}"""

    else:
        # 常规问答 / 章节精读
        hits    = retrieve(question, doc_ids=doc_ids, section=section)
        hits    = expand_hits_to_parent(hits, window=1)
        context = build_context(hits)
        sources = [{"filename": h["filename"], "score": h["score"],
                    "section": h.get("section", "")} for h in hits]
        qa_hint = (f'用户当前正在阅读【{section}】章节，请围绕该章节内容深度解读。'
                   if section else '请根据参考文档内容精准回答用户问题。')
        prompt = f"""你是专业学术文献助手。{qa_hint}

规则：
- 只使用下方参考文档中的内容，不引入外部知识
- 默认用中文回答；用户若用英文提问可用英文回答
- 回答结构清晰，引用信息时注明来源章节
- 若文档中没有相关内容，明确说明"提供的文档中未找到该信息"

参考文档：
{context}

用户问题：{question}"""

    # ── 调用 LLM（统一入口，client 和 model_id 由路由决定）──
    if not stream:
        resp = client.chat.completions.create(
            model=model_id, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content, sources

    def _stream_gen(p=prompt, s=sources, c=client, m=model_id):
        resp = c.chat.completions.create(
            model=m, max_tokens=4096,
            messages=[{"role": "user", "content": p}],
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        yield {"sources": s}

    return _stream_gen()
