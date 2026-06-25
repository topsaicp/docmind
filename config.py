import os
from pathlib import Path

BASE_DIR     = Path(__file__).parent
UPLOAD_DIR   = BASE_DIR / "uploads"
MARKDOWN_DIR = BASE_DIR / "markdown"
CHROMA_DIR   = BASE_DIR / "chroma_db"

for d in [UPLOAD_DIR, MARKDOWN_DIR, CHROMA_DIR]:
    d.mkdir(exist_ok=True)

# DeepSeek API
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

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

# LLM 模型（DeepSeek）
LLM_MODEL = "deepseek-chat"   # 或 deepseek-reasoner
