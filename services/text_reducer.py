"""
学术语言优化服务：表达优化 / 深度改写 / 综合优化
支持 SSE 流式输出，按段落+句子边界分块处理
"""
import re
from openai import OpenAI
from config import MODEL_ROUTES, MODEL_FALLBACK

_PROMPTS = {
    "ai": (
        "You are an expert academic editor. Rewrite the following English academic text to improve its "
        "clarity, natural flow, and academic register, while preserving every idea, argument, citation, "
        "statistic, and data point exactly.\n\n"
        "Apply these techniques:\n"
        "- Vary sentence length naturally: alternate concise sentences with longer, more complex ones "
        "where it improves readability\n"
        "- Replace generic or repetitive phrasing with precise, contextual academic language\n"
        "- Use appropriate academic hedging where warranted: \"suggests,\" \"appears to,\" \"it is worth "
        "noting\"\n"
        "- Diversify transitional phrases and sentence openers for better readability\n"
        "- Let disciplinary voice and natural authorial perspective come through where appropriate\n"
        "- Preserve all in-text citations (e.g., [1], (Smith, 2020)), statistics, and proper nouns unchanged\n\n"
        "Return ONLY the rewritten text. No explanation, no preamble, no commentary."
    ),
    "dup": (
        "You are an expert academic writing coach. Thoroughly rewrite the following English academic text "
        "so the ideas are expressed in fresh, original wording and sentence structure — not closely "
        "mirroring the original phrasing — while preserving every idea, argument, fact, and data point.\n\n"
        "Apply these techniques:\n"
        "- Restructure each sentence — vary word order, subordination, clause arrangement\n"
        "- Alternate between active and passive voice where it improves clarity\n"
        "- Choose discipline-appropriate synonyms and phrasing rather than close paraphrase\n"
        "- Change nominalization patterns (e.g., \"the analysis of results\" → \"analyzing the results\")\n"
        "- Vary paragraph-opening strategies throughout\n"
        "- Reorganize information within paragraphs where the logic still holds\n"
        "- Preserve all in-text citations (e.g., [1], (Smith, 2020)), numerical data, and proper nouns "
        "unchanged\n\n"
        "Return ONLY the rewritten text. No explanation, no preamble, no commentary."
    ),
    "both": (
        "You are an expert academic writer and editor. Perform a comprehensive rewrite of the following "
        "English academic text to maximize both readability and originality of expression, while "
        "preserving every idea, argument, citation, statistic, and data point exactly.\n\n"
        "Apply all of the following:\n"
        "- Restructure sentences for natural flow and originality of phrasing\n"
        "- Vary sentence length and rhythm throughout\n"
        "- Replace vocabulary with contextually precise, discipline-specific alternatives\n"
        "- Add natural academic hedging and diversified transitional language\n"
        "- Vary clause ordering, subordination, and information sequencing throughout\n"
        "- Reorganize paragraph flow where logical\n"
        "- Preserve all in-text citations (e.g., [1], (Smith, 2020)), numerical data, and proper "
        "nouns unchanged\n\n"
        "Return ONLY the rewritten text. No explanation, no preamble, no commentary."
    ),
}

_CHUNK_WORDS = {"ai": 500, "dup": 450, "both": 350}  # 适当缩小，避免 Groq TPM 超限
_SPEED_MIN   = {"ai": 2.0, "dup": 2.0, "both": 3.0}

_primary_client:  OpenAI | None = None
_fallback_client: OpenAI | None = None


def _get_primary() -> tuple[OpenAI, str]:
    global _primary_client
    key, base, model = MODEL_ROUTES.get("writing", MODEL_ROUTES["qa"])
    if _primary_client is None:
        _primary_client = OpenAI(api_key=key, base_url=base)
    return _primary_client, model


def _get_fallback() -> tuple[OpenAI, str]:
    global _fallback_client
    key, base, model = MODEL_FALLBACK.get("writing", MODEL_ROUTES["qa"])
    if _fallback_client is None:
        _fallback_client = OpenAI(api_key=key, base_url=base)
    return _fallback_client, model


def _is_rate_limit(e: Exception) -> bool:
    err = str(e).lower()
    return "429" in err or "rate_limit" in err or "rate limit" in err


def _stream_chunk(client: OpenAI, model: str, system_prompt: str, chunk: str):
    """流式输出单块改写结果，返回 (delta_generator, error_or_None)"""
    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": chunk},
        ],
        max_tokens=4096,
        stream=True,
    )


def estimate_minutes(text: str, mode: str) -> float:
    wc   = len(text.split())
    rate = _SPEED_MIN.get(mode, 2.0)
    return round(wc / 1000 * rate, 1)


def detect_language(text: str) -> str:
    if not text.strip():
        return "unknown"
    ascii_cnt = sum(1 for c in text if ord(c) < 128 and c.isprintable())
    return "en" if ascii_cnt / max(len(text), 1) > 0.82 else "other"


def _split_chunks(text: str, max_words: int) -> list[str]:
    """Split text at paragraph/sentence boundaries, keeping each chunk ≤ max_words."""
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    chunks: list[str] = []
    cur_paras: list[str] = []
    cur_words = 0

    for para in paragraphs:
        pw = len(para.split())
        if cur_words + pw <= max_words:
            cur_paras.append(para)
            cur_words += pw
        else:
            if cur_paras:
                chunks.append('\n\n'.join(cur_paras))
            if pw <= max_words:
                cur_paras, cur_words = [para], pw
            else:
                sents = re.split(r'(?<=[.!?])\s+', para)
                sub: list[str] = []
                sw = 0
                for s in sents:
                    ssw = len(s.split())
                    if sw + ssw <= max_words:
                        sub.append(s); sw += ssw
                    else:
                        if sub:
                            chunks.append(' '.join(sub))
                        sub, sw = [s], ssw
                cur_paras = [' '.join(sub)] if sub else []
                cur_words  = sw

    if cur_paras:
        chunks.append('\n\n'.join(cur_paras))
    return [c for c in chunks if c.strip()]


def reduce_stream(text: str, mode: str):
    """
    Generator yielding event dicts for SSE:
      {"type":"meta",        "total_chunks":N, "total_words":W, "est_minutes":M}
      {"type":"chunk_start", "chunk_idx":i,   "total":N}
      {"type":"text",        "text":"..."}
      {"type":"chunk_done",  "chunk_idx":i}
      {"type":"done"}
      {"type":"error",       "text":"..."}
    """
    if mode not in _PROMPTS:
        yield {"type": "error", "text": f"未知模式: {mode}"}
        return

    if detect_language(text) != "en":
        yield {"type": "error", "text": "仅支持英文文本，请检查输入内容"}
        return

    max_words  = _CHUNK_WORDS[mode]
    chunks     = _split_chunks(text, max_words)
    total      = len(chunks)
    word_count = len(text.split())

    yield {
        "type": "meta",
        "total_chunks": total,
        "total_words":  word_count,
        "est_minutes":  estimate_minutes(text, mode),
    }

    system_prompt = _PROMPTS[mode]

    for idx, chunk in enumerate(chunks):
        yield {"type": "chunk_start", "chunk_idx": idx, "total": total}

        # 先用主路由，429 时自动切换到备用路由
        client, model_id = _get_primary()
        used_fallback = False
        try:
            stream = _stream_chunk(client, model_id, system_prompt, chunk)
        except Exception as e:
            if _is_rate_limit(e):
                client, model_id = _get_fallback()
                used_fallback = True
                try:
                    stream = _stream_chunk(client, model_id, system_prompt, chunk)
                except Exception as e2:
                    yield {"type": "error", "text": f"第 {idx+1}/{total} 块失败（主备均不可用）: {e2}"}
                    return
            else:
                yield {"type": "error", "text": f"第 {idx+1}/{total} 块处理失败: {e}"}
                return

        if used_fallback:
            yield {"type": "text", "text": ""}  # 通知前端已切换（内容不影响）
            print(f"[reduce] 块 {idx+1}/{total} 切换到备用模型 {model_id}")

        try:
            for part in stream:
                delta = part.choices[0].delta.content
                if delta:
                    yield {"type": "text", "text": delta}
        except Exception as e:
            if _is_rate_limit(e) and not used_fallback:
                # 流式中途遇到 429，当前块已输出部分内容，换行后用备用模型重新生成该块
                yield {"type": "text", "text": "\n\n[切换到备用模型重新生成此块]\n\n"}
                fb_client, fb_model = _get_fallback()
                try:
                    fb_stream = _stream_chunk(fb_client, fb_model, system_prompt, chunk)
                    for part in fb_stream:
                        delta = part.choices[0].delta.content
                        if delta:
                            yield {"type": "text", "text": delta}
                except Exception as e3:
                    yield {"type": "error", "text": f"第 {idx+1}/{total} 块备用模型也失败: {e3}"}
                    return
            else:
                yield {"type": "error", "text": f"第 {idx+1}/{total} 块流式失败: {e}"}
                return

        yield {"type": "chunk_done", "chunk_idx": idx}

    yield {"type": "done"}
