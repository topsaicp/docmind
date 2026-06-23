"""
问答接口
POST /api/ask            → 普通问答
POST /api/ask/stream     → SSE 流式问答
GET  /api/stats          → 知识库统计
GET  /api/documents/{id}/sections → 某文档的章节列表
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
import json

from services.retriever import ask
from services.embedder  import collection_count, get_doc_sections
from db.database import get_session, User
from config import FREE_QUERY_DAILY_LIMIT
from routers.auth import get_current_user

router = APIRouter(prefix="/api", tags=["query"])


def _check_and_count_query(user: User, session: Session):
    db_user = session.query(User).filter_by(id=user.id).first()
    today   = datetime.utcnow().date().isoformat()
    if db_user.query_date != today:
        db_user.query_count_today = 0
        db_user.query_date        = today
    if db_user.plan == "free" and db_user.query_count_today >= FREE_QUERY_DAILY_LIMIT:
        raise HTTPException(403, f"今日提问次数已达上限（{FREE_QUERY_DAILY_LIMIT}次），请明天再来或升级专业版")
    db_user.query_count_today += 1
    session.commit()


class AskRequest(BaseModel):
    question: str
    top_k:    int       = 5
    doc_ids:  list[str] = []   # 空 = 全库；非空 = 指定文档
    section:  str       = ""   # 非空 = 只检索该章节


@router.post("/ask")
def ask_question(req: AskRequest, session: Session = Depends(get_session),
                 current_user: User = Depends(get_current_user)):
    if not req.question.strip():
        return {"error": "问题不能为空"}
    _check_and_count_query(current_user, session)
    answer, sources = ask(
        req.question,
        doc_ids = req.doc_ids or None,
        section = req.section or None,
    )
    return {"question": req.question, "answer": answer, "sources": sources}


@router.post("/ask/stream")
def ask_stream(req: AskRequest, session: Session = Depends(get_session),
               current_user: User = Depends(get_current_user)):
    if not req.question.strip():
        return {"error": "问题不能为空"}
    _check_and_count_query(current_user, session)

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
def get_sections(doc_id: str, current_user: User = Depends(get_current_user)):
    sections = get_doc_sections(doc_id)
    return {"doc_id": doc_id, "sections": sections}


@router.get("/stats")
def get_stats(current_user: User = Depends(get_current_user)):
    return {"total_vectors": collection_count()}
