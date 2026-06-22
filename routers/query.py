"""
问答接口
POST /api/ask            → 普通问答
POST /api/ask/stream     → SSE 流式问答
GET  /api/stats          → 知识库统计
GET  /api/documents/{id}/sections → 某文档的章节列表
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json

from services.retriever import ask
from services.embedder  import collection_count, get_doc_sections

router = APIRouter(prefix="/api", tags=["query"])


class AskRequest(BaseModel):
    question: str
    top_k:    int       = 5
    doc_ids:  list[str] = []   # 空 = 全库；非空 = 指定文档
    section:  str       = ""   # 非空 = 只检索该章节


@router.post("/ask")
def ask_question(req: AskRequest):
    if not req.question.strip():
        return {"error": "问题不能为空"}
    answer, sources = ask(
        req.question,
        doc_ids = req.doc_ids or None,
        section = req.section or None,
    )
    return {"question": req.question, "answer": answer, "sources": sources}


@router.post("/ask/stream")
def ask_stream(req: AskRequest):
    if not req.question.strip():
        return {"error": "问题不能为空"}

    doc_ids = req.doc_ids or None
    section = req.section or None

    def event_generator():
        try:
            gen = ask(req.question, stream=True, doc_ids=doc_ids, section=section)
            for chunk in gen:
                if isinstance(chunk, dict):
                    yield f"data: {json.dumps({'type':'sources','sources':chunk['sources']}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type':'text','text':chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = f"❌ 服务器错误：{str(e)}"
            yield f"data: {json.dumps({'type':'text','text':err}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/documents/{doc_id}/sections")
def get_sections(doc_id: str):
    """返回该文档包含的章节名列表"""
    sections = get_doc_sections(doc_id)
    return {"doc_id": doc_id, "sections": sections}


@router.get("/stats")
def get_stats():
    return {"total_vectors": collection_count()}
