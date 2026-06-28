import os
from pathlib import Path

BASE_DIR     = Path(__file__).parent
UPLOAD_DIR   = BASE_DIR / "uploads"
MARKDOWN_DIR = BASE_DIR / "markdown"
CHROMA_DIR   = BASE_DIR / "chroma_db"

for d in [UPLOAD_DIR, MARKDOWN_DIR, CHROMA_DIR]:
    d.mkdir(exist_ok=True)

# ── API Keys ──────────────────────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY",      "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY",  "")   # 备用
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")   # 预留：Claude
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY",    "")   # 预留：GPT-4o

GROQ_BASE_URL      = "https://api.groq.com/openai/v1"
DEEPSEEK_BASE_URL  = "https://api.deepseek.com"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
OPENAI_BASE_URL    = "https://api.openai.com/v1"

# ── 多模型路由表 ───────────────────────────────────────────────────────
# 每项格式: (api_key, base_url, model_id)
# 当前：全部使用 Groq；接入新模型时只改此处，业务代码无需动
MODEL_ROUTES: dict[str, tuple[str, str, str]] = {
    # 任务          api_key        base_url           model_id
    # -------------------------------------------------------------------
    # 普通问答：Groq 速度快，llama-3.3-70b 中英文能力强
    "qa":       (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
    # 多文档对比：长上下文推理
    "multi":    (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
    # 文献综述：长文生成
    "review":   (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
    # 降率写作：流式改写
    "writing":  (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
    # 引用提取：轻量结构化任务
    "cite":     (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
}

# Embedding 模型：英文为主混合场景用 BGE-M3
JINA_API_KEY = os.getenv("JINA_API_KEY", "")

# Chroma collection 名称
COLLECTION_NAME = "kb_collection"

# ── 分块参数（子块用于向量化检索，父块用于拼 Prompt）──
CHILD_CHUNK_ZH   = 280   # 子块：中文字符数
CHILD_CHUNK_EN   = 480   # 子块：英文字符数
CHUNK_OVERLAP_ZH = 55    # 滑动窗口重叠：中文字符（~19%）
CHUNK_OVERLAP_EN = 90    # 滑动窗口重叠：英文字符（~18%）
PARENT_WINDOW    = 3     # 父块 = 连续 N 个子块，检索命中后展开上下文
# 旧常量保留，兼容 upload.py 等处的引用
CHUNK_SIZE_EN = 200
CHUNK_SIZE_ZH = 500
CHUNK_OVERLAP  = 30

# ── 检索参数 ──
TOP_K        = 8       # 初次检索数量
RERANK_TOP_K = 5       # Rerank 后保留数量

# 用户认证
SECRET_KEY   = os.getenv("SECRET_KEY", "docmind-dev-secret-change-in-prod")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")
JWT_EXPIRE_DAYS = 30

# 免费套餐限额
FREE_PDF_LIMIT         = 5
FREE_QUERY_DAILY_LIMIT = 20

# 文件限制
MAX_FILE_SIZE_MB = 50
ALLOWED_EXT      = {".pdf"}

# 默认 LLM（仅用于路由表之外的兜底调用）
LLM_MODEL = "llama-3.3-70b-versatile"
