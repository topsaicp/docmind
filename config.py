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

# 分块参数
CHUNK_SIZE_EN = 200    # 英文：词数
CHUNK_SIZE_ZH = 500    # 中文：字符数
CHUNK_OVERLAP  = 30    # overlap（词/字符，取小值）

# 检索参数
TOP_K        = 8       # 初次检索数量
RERANK_TOP_K = 5       # Rerank 后保留数量

# 文件限制
MAX_FILE_SIZE_MB = 50
ALLOWED_EXT      = {".pdf"}

# LLM 模型（DeepSeek）
LLM_MODEL = "deepseek-chat"   # 或 deepseek-reasoner
