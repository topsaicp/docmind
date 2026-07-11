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
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY",  "")
GLM_API_KEY       = os.getenv("GLM_API_KEY", "")         # 智谱 GLM 视觉分析
GEMINI_API_KEY    = GLM_API_KEY                           # 兼容旧引用
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")   # 预留
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY",    "")   # 预留

GROQ_BASE_URL      = "https://api.groq.com/openai/v1"
DEEPSEEK_BASE_URL  = "https://api.deepseek.com"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
OPENAI_BASE_URL    = "https://api.openai.com/v1"
GEMINI_BASE_URL    = "https://open.bigmodel.cn/api/paas/v4/"   # 智谱 GLM

# ── 多模型路由表 ───────────────────────────────────────────────────────
# 每项格式: (api_key, base_url, model_id)
# 当前：全部使用 Groq；接入新模型时只改此处，业务代码无需动
MODEL_ROUTES: dict[str, tuple[str, str, str]] = {
    # 任务          api_key              base_url             model_id
    # -------------------------------------------------------------------
    "qa":       (DEEPSEEK_API_KEY,  DEEPSEEK_BASE_URL,  "deepseek-v4-flash"),
    "multi":    (DEEPSEEK_API_KEY,  DEEPSEEK_BASE_URL,  "deepseek-v4-flash"),
    "review":   (DEEPSEEK_API_KEY,  DEEPSEEK_BASE_URL,  "deepseek-v4-flash"),
    "writing":  (GROQ_API_KEY,      GROQ_BASE_URL,      "llama-3.3-70b-versatile"),
    "cite":     (DEEPSEEK_API_KEY,  DEEPSEEK_BASE_URL,  "deepseek-v4-flash"),
}

# 429/限流时自动降级到此备用路由
MODEL_FALLBACK: dict[str, tuple[str, str, str]] = {
    "qa":       (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.1-8b-instant"),
    "multi":    (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.1-8b-instant"),
    "review":   (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.3-70b-versatile"),
    "writing":  (DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, "deepseek-v4-flash"),
    "cite":     (GROQ_API_KEY,  GROQ_BASE_URL,  "llama-3.1-8b-instant"),
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

# 邮件服务（Resend）
RESEND_API_KEY      = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM          = os.getenv("EMAIL_FROM", "DocMind <noreply@topsai.app>")
APP_URL             = os.getenv("APP_URL", "http://localhost:8000")   # 生产环境必须设置
EMAIL_VERIFY_HOURS  = 24   # 验证链接有效期（小时）

# ── 套餐限额配置表 ────────────────────────────────────────────────────
PLAN_LIMITS: dict[str, dict] = {
    "free": {
        "max_tokens":        2048,
        "review_chunks":     6,
        "review_max_docs":   3,
        "reduce_max_words":  800,
        "daily_query_limit": 20,
        "pdf_limit":         5,
    },
    "plus": {
        "max_tokens":        4096,
        "review_chunks":     8,
        "review_max_docs":   10,
        "reduce_max_words":  3000,
        "daily_query_limit": 100,
        "pdf_limit":         30,
    },
    "pro": {
        "max_tokens":        8192,
        "review_chunks":     12,
        "review_max_docs":   20,
        "reduce_max_words":  10000,
        "daily_query_limit": 999999,
        "pdf_limit":         100,
    },
    "enterprise": {
        "max_tokens":        8192,
        "review_chunks":     12,
        "review_max_docs":   20,
        "reduce_max_words":  10000,
        "daily_query_limit": 999999,
        "pdf_limit":         500,
    },
}

def get_limits(plan: str) -> dict:
    """返回套餐对应的限制参数；admin 视为 pro；未知套餐视为 free。"""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

# 兼容旧引用
FREE_PDF_LIMIT         = PLAN_LIMITS["free"]["pdf_limit"]
FREE_QUERY_DAILY_LIMIT = PLAN_LIMITS["free"]["daily_query_limit"]

# 文件限制
MAX_FILE_SIZE_MB = 50
ALLOWED_EXT      = {".pdf"}

# 默认 LLM（仅用于路由表之外的兜底调用）
LLM_MODEL = "llama-3.3-70b-versatile"
