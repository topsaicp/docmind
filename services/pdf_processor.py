"""
PDF处理：扫描件检测 → 结构化文字提取 → 清洗 → 分块
- 英文 PDF：PyMuPDF 字体大小识别章节标题，保留 Abstract/Introduction/... 结构
- 中文 PDF：markitdown 提取（效果好）
- 扫描件：PyMuPDF OCR 兜底
"""
import re
from collections import Counter
import fitz                      # PyMuPDF
from pathlib import Path
from config import CHUNK_SIZE_EN, CHUNK_SIZE_ZH, CHUNK_OVERLAP

# 英文学术论文常见章节名（匹配无编号的节标题）
_EN_SECTION_RE = re.compile(
    r'^(abstract|introduction|background|related work|literature review|'
    r'prior work|preliminary|preliminaries|motivation|problem (statement|formulation)|'
    r'methodology|methods?|materials? and methods?|experimental (setup|design)|'
    r'experiments?|evaluation|implementation|system (design|overview)|'
    r'results?|results? and discussion|analysis|performance evaluation|'
    r'discussion|limitations?|future work|conclusions?|summary|'
    r'acknowledgements?|acknowledgments?|funding|'
    r'references?|bibliography|appendix\s*[a-z]?)\s*$',
    re.IGNORECASE
)

# 匹配带编号的章节："1. Introduction" / "3.1. System Model" / "A. Appendix"
_EN_NUMBERED_RE = re.compile(
    r'^(\d+\.(\d+\.)*|[A-Z]\.\s)\s*\S{2,}',
)

# 英文节名 → 双语标准化（让中文查询也能命中英文论文章节）
_SECTION_BILINGUAL = {
    'abstract':             'Abstract（摘要）',
    'introduction':         'Introduction（引言/简介）',
    'background':           'Background（背景）',
    'related work':         'Related Work（相关工作）',
    'literature review':    'Literature Review（文献综述）',
    'prior work':           'Prior Work（前期工作）',
    'methodology':          'Methodology（研究方法）',
    'method':               'Method（方法）',
    'methods':              'Methods（方法）',
    'materials and methods':'Materials and Methods（材料与方法）',
    'experimental setup':   'Experimental Setup（实验设置）',
    'experiments':          'Experiments（实验）',
    'evaluation':           'Evaluation（评估）',
    'results':              'Results（结果）',
    'results and discussion':'Results and Discussion（结果与讨论）',
    'discussion':           'Discussion（讨论）',
    'analysis':             'Analysis（分析）',
    'performance evaluation':'Performance Evaluation（性能评估）',
    'conclusion':           'Conclusion（结论）',
    'conclusions':          'Conclusions（结论）',
    'future work':          'Future Work（未来工作）',
    'limitations':          'Limitations（局限性）',
    'acknowledgements':     'Acknowledgements（致谢）',
    'acknowledgments':      'Acknowledgments（致谢）',
    'references':           'References（参考文献）',
    'bibliography':         'Bibliography（参考文献）',
    'appendix':             'Appendix（附录）',
    'system overview':      'System Overview（系统概述）',
    'implementation':       'Implementation（实现）',
    'summary':              'Summary（总结）',
}

def _bilingual_section(raw: str) -> str:
    """将节名转为 '英文（中文）' 双语格式；去掉编号前缀"""
    name = re.sub(r'^(\d+\.)+\s*|^[A-Z]\.\s*', '', raw).strip()
    key  = name.lower().rstrip(':').strip()
    return _SECTION_BILINGUAL.get(key, name)

# 这些章节对问答无意义，不写入向量库（参考文献、致谢等）
_SKIP_SECTIONS = {
    'References（参考文献）',
    'Bibliography（参考文献）',
    'Acknowledgements（致谢）',
    'Acknowledgments（致谢）',
    'Declaration of Competing Interest',
    'Data availability',
}


# ── 工具函数 ────────────────────────────────────────────────────────────
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


# ── 英文 PDF：结构化提取（保留章节标题）──────────────────────────────
def _extract_en_structured(pdf_path: str) -> str:
    """
    用 PyMuPDF dict 模式逐块提取，按字体大小识别标题。
    标题行加 ## 前缀，方便后续按节分块。
    """
    doc = fitz.open(pdf_path)
    out = []

    for page in doc:
        # sort=True 让 PyMuPDF 按阅读顺序（从上到下从左到右）排列块
        blocks = page.get_text("dict", sort=True)["blocks"]

        # 收集本页所有非空 span 的字体大小，用众数作为正文基准
        sizes = []
        for blk in blocks:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes.append(round(span["size"], 1))

        base_size = Counter(sizes).most_common(1)[0][0] if sizes else 10.0

        for blk in blocks:
            if blk["type"] != 0:      # 跳过图片块
                continue

            # 拼接块内文本 & 记录最大字号 & 是否全块加粗
            lines_text = []
            blk_max_size = 0.0
            bold_spans = 0
            total_spans = 0
            for line in blk["lines"]:
                parts = []
                for span in line["spans"]:
                    t = span["text"]
                    if t.strip():
                        parts.append(t)
                        blk_max_size = max(blk_max_size, span["size"])
                        total_spans += 1
                        if span["flags"] & 16:   # 粗体标志位
                            bold_spans += 1
                if parts:
                    lines_text.append("".join(parts))

            if not lines_text:
                continue

            blk_text = " ".join(lines_text).strip()
            if not blk_text:
                continue

            is_all_bold = total_spans > 0 and bold_spans == total_spans

            # 判断是否为章节标题：
            # 条件1：字体比正文大 ≥15%（大号标题）
            # 条件2：全块粗体 + 短行 + 匹配章节名或编号格式
            # 条件3：无编号但匹配常见章节名（如 "Abstract"）
            is_heading = len(blk_text) < 150 and (
                blk_max_size >= base_size * 1.15
                or (is_all_bold and (
                        _EN_SECTION_RE.match(blk_text)
                        or _EN_NUMBERED_RE.match(blk_text)
                   ))
                or _EN_SECTION_RE.match(blk_text)
            )

            if is_heading:
                out.append(f"\n## {blk_text}\n")
            else:
                out.append(blk_text)

    doc.close()
    # 合并，去掉开头多余换行
    return "\n\n".join(filter(None, out)).strip()


# ── 中文 PDF：markitdown 提取 ────────────────────────────────────────
def _extract_zh(pdf_path: str) -> str:
    try:
        from markitdown import MarkItDown
        return MarkItDown().convert(pdf_path).text_content.strip()
    except Exception:
        return _extract_en_structured(pdf_path)   # 降级


# ── 扫描件：PyMuPDF OCR ───────────────────────────────────────────────
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


# ── 主提取入口 ────────────────────────────────────────────────────────
def extract_text(pdf_path: str) -> str:
    if is_scanned(pdf_path):
        return _extract_ocr(pdf_path)

    # 取前两页判断语言
    doc  = fitz.open(pdf_path)
    sample = "".join(doc[i].get_text() for i in range(min(2, len(doc))))
    doc.close()

    if detect_language(sample) == "zh":
        return _extract_zh(pdf_path)
    else:
        return _extract_en_structured(pdf_path)


# ── 清洗 ─────────────────────────────────────────────────────────────
def clean_text(text: str, lang: str = "en") -> str:
    # 去掉纯数字行（页码）
    text = re.sub(r'(?m)^\s*\d+\s*$', '', text)
    # 英文：修复行末连字符断词（"meth-\nod" → "method"）
    if lang == "en":
        text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    # 压缩多余空行（保留 ## 标记行前后的空行）
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [l.strip() for l in text.split('\n')]
    return '\n'.join(lines).strip()


# ── 分块（章节感知）──────────────────────────────────────────────────
def chunk_text(text: str, doc_id: str, filename: str) -> list[dict]:
    """
    1. 按 ## 节标题切分（英文结构化提取后自带；中文按段落切）
    2. 节内按大小合并段落
    3. 每块 text 前缀加 [节名]，帮助检索时定位章节
    """
    lang  = detect_language(text[:500])
    limit = CHUNK_SIZE_ZH if lang == "zh" else CHUNK_SIZE_EN * 6  # 词数*6≈字符数
    chunks    = []
    chunk_idx = 0

    def flush(buf: str, section: str = ""):
        nonlocal chunk_idx
        buf = buf.strip()
        if len(buf) < 30:
            return
        bi = _bilingual_section(section) if section else ""
        # 跳过对问答无价值的章节
        if bi in _SKIP_SECTIONS:
            return
        full = f"{bi}:\n{buf}" if bi else buf
        chunks.append({
            "id":       f"{doc_id}_{chunk_idx}",
            "doc_id":   doc_id,
            "filename": filename,
            "chunk_id": chunk_idx,
            "text":     full,
            "lang":     detect_language(full),
            "section":  bi,
        })
        chunk_idx += 1

    # 先按 ## 切节
    raw_sections = re.split(r'\n##\s+', '\n' + text)
    for part in raw_sections:
        part = part.strip()
        if not part:
            continue

        # 第一行是节名（来自 ## 标题），其余是正文
        first_nl = part.find('\n')
        if first_nl > 0 and first_nl < 100:
            section_name = part[:first_nl].strip().rstrip(':').strip()
            body = part[first_nl:]
        else:
            section_name = ""
            body = part

        # 摘要/结论等关键节用更大的 limit，避免切碎
        bi_name = _bilingual_section(section_name) if section_name else ""
        _BIG_SECTIONS = {'Abstract（摘要）', 'Conclusion（结论）', 'Conclusions（结论）',
                         'Summary（总结）', 'Introduction（引言/简介）'}
        sec_limit = limit * 2 if bi_name in _BIG_SECTIONS else limit

        # 节内按空行切段落，合并到 limit
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', body) if p.strip()]
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 <= sec_limit:
                current += ("\n\n" if current else "") + para
            else:
                flush(current, section_name)
                if len(para) > sec_limit:
                    step = max(sec_limit - CHUNK_OVERLAP * 6, 100)
                    for i in range(0, len(para), step):
                        flush(para[i:i + sec_limit], section_name)
                    current = para[-(CHUNK_OVERLAP * 6):]
                else:
                    current = para

        flush(current, section_name)

    return chunks


# ── 对外主接口 ────────────────────────────────────────────────────────
def process_pdf(pdf_path: str, doc_id: str, filename: str) -> tuple[list[dict], int]:
    page_count = get_page_count(pdf_path)
    raw_text   = extract_text(pdf_path)
    lang       = detect_language(raw_text[:500])
    clean      = clean_text(raw_text, lang)
    chunks     = chunk_text(clean, doc_id, filename)
    return chunks, page_count
