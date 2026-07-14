# DocMindAI · PDF 知识库问答

基于 RAG 技术的私有化 PDF 知识库问答系统，支持按章节精读与 AI 智能问答。

## 功能特性

- 📄 上传 PDF，自动解析章节结构（中英文均支持）
- 📖 按章节定向解读（摘要、引言、方法、结论等）
- 🔍 跨文献智能检索与对比分析
- 🌐 中英双语检索
- 💾 对话历史本地持久化
- 🔒 完全私有部署，数据不出境

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | 纯 HTML/CSS/JS（单文件） |
| 后端 | FastAPI + SQLite |
| Embedding | BCEmbedding（本地，768维） |
| 向量库 | Chroma |
| PDF 提取 | PyMuPDF（英文）+ MarkItDown（中文） |
| LLM | DeepSeek API |

## 快速启动

### 1. 安装依赖

```bash
pip install fastapi uvicorn sqlalchemy chromadb sentence-transformers \
            pymupdf markitdown openai python-multipart
```

### 2. 下载 Embedding 模型

```bash
mkdir -p /root/models/bce-embedding
# 从 HuggingFace 下载 maidalun1020/bce-embedding-base_v1
# 或使用镜像：https://hf-mirror.com
```

### 3. 配置环境变量

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
```

### 4. 启动服务

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000` 即可使用。

## 项目结构

```
kb_project/
├── main.py                 # FastAPI 入口
├── config.py               # 配置（读取环境变量）
├── frontend/
│   └── index.html          # 前端页面
├── routers/
│   ├── upload.py           # 上传接口
│   └── query.py            # 问答接口
├── services/
│   ├── pdf_processor.py    # PDF 提取与分块
│   ├── embedder.py         # 向量化与检索
│   └── retriever.py        # RAG 问答
└── db/
    └── database.py         # 数据模型
```

## 支付与授权

详见页面定价方案。联系：njjimchen@protonmail.com
