"""
PDF处理：扫描件检测 → 结构化文字提取 → 清洗 → 分块 (v2)

分块策略（五项升级）：
  1. 章节感知切分     — 绝不跨章节合并块
  2. 句子边界切分     — 断点落在句末标点，不从句子中间截断
  3. 滑动窗口重叠     — 相邻块保留 ~18% 重叠，跨块语义衔接
  4. LaTeX/表格保护  — 公式与表格视为原子单元，禁止从中间截断
  5. 元数据前缀注入  — 每块文本开头拼入 [文档: X][章节: Y]，
                       Jina 编码时同时捕获结构语义
  + 父子块追踪       — 每 PARENT_WINDOW 个子块构成一个父块组，
                       检索命中子块后可展开为父块上下文喂给 LLM
"""
import re
from collections import Counter
from pathlib import Path
import fitz                      # PyMuPDF
from config import (
    CHILD_CHUNK_ZH, CHILD_CHUNK_EN,
    CHUNK_OVERLAP_ZH, CHUNK_OVERLAP_EN,
    PARENT_WINDOW,
)

# ── 章节名正则 ────────────────────────────────────────────────────────
_EN_SECTION_RE = re.compile(
    r'^(abstract|introduction|background|related work|literature review|'
    r'prior work|preliminary|preliminaries|motivation|problem (statement|formulation)|'
    r'methodology|methods?|materials? and methods?|experimental (setup|design)|'
    r'experiments?|evaluation|implementation|system (design|overview)|'
    r'results?|results? and discussion|analysis|performance evaluation|'
    r'discussion|limitations?|future work|conclusions?|summary|'
    r'acknowledgements?|acknowledgments?|funding|'
    r'references?|bibliography|appendix\s*[a-z]?)\s*$',
    re.IGNORECASE,
)
_EN_NUMBERED_RE = re.compile(r'^(\d+\.(\d+\.)*|[A-Z]\.\s)\s*\S{2,}')

_SECTION_BILINGUAL = {
    'abstract':              'Abstract（摘要）',
    'introduction':          'Introduction（引言/简介）',
    'background':            'Background（背景）',
    'related work':          'Related Work（相关工作）',
    'literature review':     'Literature Review（文献综述）',
    'prior work':            'Prior Work（前期工作）',
    'methodology':           'Methodology（研究方法）',
    'method':                'Method（方法）',
    'methods':               'Methods（方法）',
    'materials and methods': 'Materials and Methods（材料与方法）',
    'experimental setup':    'Experimental Setup（实验设置）',
    'experiments':           'Experiments（实验）',
    'evaluation':            'Evaluation（评估）',
    'results':               'Results（结果）',
    'results and discussion':'Results and Discussion（结果与讨论）',
    'discussion':            'Discussion（讨论）',
    'analysis':              'Analysis（分析）',
    'performance evaluation':'Performance Evaluation（性能评估）',
    'conclusion':            'Conclusion（结论）',
    'conclusions':           'Conclusions（结论）',
    'future work':           'Future Work（未来工作）',
    'limitations':           'Limitations（局限性）',
    'acknowledgements':      'Acknowledgements（致谢）',
    'acknowledgments':       'Acknowledgments（致谢）',
    'references':            'References（参考文献）',
    'bibliography':          'Bibliography（参考文献）',
    'appendix':              'Appendix（附录）',
    'system overview':       'System Overview（系统概述）',
    'implementation':        'Implementation（实现）',
    'summary':               'Summary（总结）',
}

_SKIP_SECTIONS = {
    'References（参考文献）',
    'Bibliography（参考文献）',
    'Acknowledgements（致谢）',
    'Acknowledgments（致谢）',
    'Declaration of Competing Interest',
    'Data availability',
}

_BIG_SECTIONS = {
    'Abstract（摘要）', 'Conclusion（结论）', 'Conclusions（结论）',
    'Summary（总结）', 'Introduction（引言/简介）',
}


def _bilingual_section(raw: str) -> str:
    name = re.sub(r'^(\d+\.)+\s*|^[A-Z]\.\s*', '', raw).strip()
    key  = name.lower().rstrip(':').strip()
    return _SECTION_BILINGUAL.get(key, name)


# ── 工具函数 ────────────────────────────────────────────────────────
def is_scanned(pdf_path: str, sample_pages: int = 3) -> bool:
    doc   = fitz.open(pdf_path)
    pages = min(sample_pages, len(doc))
    total = sum(len(doc[i].get_text().strip()) for i in range(pages))
    doc.close()
    return total < 50 * pages


def get_page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    n   = len(doc)
    doc.close()
    return n


def detect_language(text: str) -> str:
    if not text:
        return "en"
    zh_chars = len(re.findall(r'[一-鿿]', text))
    return "zh" if zh_chars / max(len(text), 1) > 0.15 else "en"


# ── LaTeX 与表格原子化保护 ────────────────────────────────────────────
_PH_RE = re.compile(r'\x00PH\d{4}\x00')


def _protect_special(text: str) -> tuple[str, dict]:
    """
    将 LaTeX 公式和 Markdown 表格替换为不可分割的占位符，
    防止分块算法从中间截断，导致向量化语义崩坏。
    """
    pmap: dict[str, str] = {}
    count = [0]

    def _sub(m: re.Match) -> str:
        key = f"\x00PH{count[0]:04d}\x00"
        pmap[key] = m.group(0)
        count[0] += 1
        return key

    # 块级 LaTeX: $$...$$ （允许跨行，限 2000 字符防过贪）
    text = re.sub(r'\$\$[\s\S]{1,2000}?\$\$', _sub, text)
    # 行内 LaTeX: $...$ （同行，限 300 字符）
    text = re.sub(r'\$[^\n$]{1,300}\$', _sub, text)
    # Markdown 表格：连续含 | 的行（含分隔行 |---|...）
    text = re.sub(r'(?m)^(\|[^\n]+\|\s*\n){2,}', _sub, text)

    return text, pmap


def _restore_special(text: str, pmap: dict) -> str:
    for key, val in pmap.items():
        text = text.replace(key, val)
    return text


# ── 句子边界分块（滑动窗口重叠）─────────────────────────────────────
def _sentence_chunks(text: str, max_chars: int, overlap_chars: int, lang: str) -> list[str]:
    """
    在句子结束标点处断块，相邻块保留末尾 overlap_chars 作为重叠。
    占位符（\x00PH...）不含句子标点，不会被错误切断。
    """
    # 按句末标点切分，保留标点附在句子末尾
    if lang == 'zh':
        pat = r'(?<=[。！？；\n])'
    else:
        pat = r'(?<=[.!?])(?=\s)'

    raw = re.split(pat, text)
    # 再按换行细分（段落边界必须是切分点）
    sentences: list[str] = []
    for seg in raw:
        for sub in seg.split('\n'):
            s = sub.strip()
            if s:
                sentences.append(s)

    if not sentences:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    cur: list[str]    = []
    cur_len: int      = 0
    sep = '' if lang == 'zh' else ' '

    for sent in sentences:
        if cur_len + len(sent) <= max_chars:
            cur.append(sent)
            cur_len += len(sent)
        else:
            if cur:
                chunks.append(sep.join(cur))
                # 滑动窗口：从末尾取句子，直至凑够 overlap_chars
                olap: list[str] = []
                olap_len = 0
                for s in reversed(cur):
                    if olap_len + len(s) <= overlap_chars:
                        olap.insert(0, s)
                        olap_len += len(s)
                    else:
                        break
                cur     = olap + [sent]
                cur_len = olap_len + len(sent)
            else:
                # 单句超过 max_chars：强制按字符切，末尾保留 overlap
                while len(sent) > max_chars:
                    chunks.append(sent[:max_chars])
                    sent = sent[max_chars - overlap_chars:]
                cur     = [sent]
                cur_len = len(sent)

    if cur:
        tail = sep.join(cur)
        if tail.strip():
            chunks.append(tail)

    return [c.strip() for c in chunks if c.strip()]


# ── 英文 PDF 结构化提取 ───────────────────────────────────────────────
def _extract_en_structured(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    out = []
    for page in doc:
        blocks = page.get_text("dict", sort=True)["blocks"]
        sizes  = []
        for blk in blocks:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes.append(round(span["size"], 1))
        base_size = Counter(sizes).most_common(1)[0][0] if sizes else 10.0

        for blk in blocks:
            if blk["type"] != 0:
                continue
            lines_text, blk_max_size, bold_spans, total_spans = [], 0.0, 0, 0
            for line in blk["lines"]:
                parts = []
                for span in line["spans"]:
                    t = span["text"]
                    if t.strip():
                        parts.append(t)
                        blk_max_size = max(blk_max_size, span["size"])
                        total_spans += 1
                        if span["flags"] & 16:
                            bold_spans += 1
                if parts:
                    lines_text.append("".join(parts))
            if not lines_text:
                continue
            blk_text    = " ".join(lines_text).strip()
            is_all_bold = total_spans > 0 and bold_spans == total_spans
            is_heading  = len(blk_text) < 150 and (
                blk_max_size >= base_size * 1.15
                or (is_all_bold and (
                    _EN_SECTION_RE.match(blk_text)
                    or _EN_NUMBERED_RE.match(blk_text)
                ))
                or _EN_SECTION_RE.match(blk_text)
            )
            out.append(f"\n## {blk_text}\n" if is_heading else blk_text)
    doc.close()
    return "\n\n".join(filter(None, out)).strip()


def _extract_zh(pdf_path: str) -> str:
    try:
        from markitdown import MarkItDown
        return MarkItDown().convert(pdf_path).text_content.strip()
    except Exception:
        return _extract_en_structured(pdf_path)


def _extract_ocr(pdf_path: str) -> str:
    doc  = fitz.open(pdf_path)
    text = ""
    for page in doc:
        tp = page.get_text("text").strip()
        if not tp:
            try:
                tp = page.get_textpage_ocr(flags=0).extractText()
            except Exception:
                tp = ""
        text += tp + "\n\n"
    doc.close()
    return text.strip()


def extract_text(pdf_path: str) -> str:
    if is_scanned(pdf_path):
        return _extract_ocr(pdf_path)
    doc    = fitz.open(pdf_path)
    sample = "".join(doc[i].get_text() for i in range(min(2, len(doc))))
    doc.close()
    return _extract_zh(pdf_path) if detect_language(sample) == "zh" \
           else _extract_en_structured(pdf_path)


def clean_text(text: str, lang: str = "en") -> str:
    text = re.sub(r'(?m)^\s*\d+\s*$', '', text)
    if lang == "en":
        text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [l.strip() for l in text.split('\n')]
    return '\n'.join(lines).strip()


# ── 分块主函数 ────────────────────────────────────────────────────────
def chunk_text(text: str, doc_id: str, filename: str) -> list[dict]:
    """
    章节感知 + 句子边界 + 滑动窗口重叠 + LaTeX/表格保护 + 元数据前缀注入
    返回 list[dict]，每块含 parent_group_id / child_seq 供父块展开使用。
    """
    lang       = detect_language(text[:500])
    max_chars  = CHILD_CHUNK_ZH  if lang == 'zh' else CHILD_CHUNK_EN
    ovlp_chars = CHUNK_OVERLAP_ZH if lang == 'zh' else CHUNK_OVERLAP_EN
    doc_stem   = Path(filename).stem          # 用于元数据前缀

    chunks:    list[dict] = []
    chunk_idx: int = 0
    group_idx: int = 0    # 父块组序号
    group_cnt: int = 0    # 当前组内已有子块数

    def flush(content: str, section: str, pmap: dict) -> None:
        nonlocal chunk_idx, group_idx, group_cnt

        content = _restore_special(content, pmap).strip()
        if len(content) < 20:
            return

        bi = _bilingual_section(section) if section else ""
        if bi in _SKIP_SECTIONS:
            return

        # ① 元数据前缀注入（Jina 编码时同时捕获文档与章节语义）
        prefix = f"[文档: {doc_stem}]"
        if bi:
            prefix += f"[章节: {bi}]"
        full_text = prefix + "\n" + content

        # ② 父子块分组（每 PARENT_WINDOW 个子块重置组）
        if group_cnt >= PARENT_WINDOW:
            group_idx += 1
            group_cnt  = 0
        parent_group_id = f"{doc_id}_g{group_idx}"

        chunks.append({
            "id":              f"{doc_id}_{chunk_idx}",
            "doc_id":          doc_id,
            "filename":        filename,
            "chunk_id":        chunk_idx,
            "text":            full_text,
            "lang":            lang,
            "section":         bi,
            "parent_group_id": parent_group_id,
            "child_seq":       group_cnt,
        })
        chunk_idx += 1
        group_cnt += 1

    # 按 ## 切节（英文 structured 提取已插入；中文按正则识别）
    raw_sections = re.split(r'\n##\s+', '\n' + text)

    for part in raw_sections:
        part = part.strip()
        if not part:
            continue

        first_nl = part.find('\n')
        if 0 < first_nl < 120:
            section_name = part[:first_nl].strip().rstrip(':').strip()
            body         = part[first_nl:]
        else:
            section_name = ""
            body         = part

        bi_name  = _bilingual_section(section_name) if section_name else ""
        # 关键章节块上限放宽 2x，避免摘要/结论碎片化
        sec_max  = max_chars * 2 if bi_name in _BIG_SECTIONS else max_chars

        # ③ LaTeX/表格原子化保护
        protected, pmap = _protect_special(body)

        # ④ 句子边界分块 + 滑动窗口重叠
        sub_chunks = _sentence_chunks(protected, sec_max, ovlp_chars, lang)
        for sc in sub_chunks:
            flush(sc, section_name, pmap)

        # 章节边界强制结束父块组（跨节不共享父块）
        if group_cnt > 0:
            group_idx += 1
            group_cnt  = 0

    return chunks


# ── 对外主接口 ────────────────────────────────────────────────────────
def process_pdf(pdf_path: str, doc_id: str, filename: str) -> tuple[list[dict], int]:
    page_count = get_page_count(pdf_path)
    raw_text   = extract_text(pdf_path)
    lang       = detect_language(raw_text[:500])
    clean      = clean_text(raw_text, lang)
    chunks     = chunk_text(clean, doc_id, filename)
    return chunks, page_count
