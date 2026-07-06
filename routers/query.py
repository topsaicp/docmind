"""
问答接口
POST /api/ask            → 普通问答
POST /api/ask/stream     → SSE 流式问答
GET  /api/stats          → 知识库统计
GET  /api/documents/{id}/sections → 某文档的章节列表
GET  /api/documents/{id}/cite     → 提取文献元数据（引用格式用）
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
import json

from services.retriever import ask
from services.embedder  import collection_count, get_doc_sections, get_doc_header
from db.database import get_session, User, Document
from config import FREE_QUERY_DAILY_LIMIT, MODEL_ROUTES, LLM_MODEL, get_limits
from routers.auth import get_current_user
from datetime import datetime


def require_verified(user: User):
    if not user.email_verified:
        raise HTTPException(403, "请先验证邮箱后再使用此功能")


def effective_plan(user: User) -> str:
    """计算用户实际套餐（考虑到期 + admin 等同 pro）。"""
    if user.is_admin:
        return "pro"
    if user.plan == "pro" and user.plan_expires_at and user.plan_expires_at < datetime.utcnow():
        return "free"
    return user.plan or "free"

router = APIRouter(prefix="/api", tags=["query"])


def _check_and_count_query(user: User, session: Session):
    db_user = session.query(User).filter_by(id=user.id).first()
    today   = datetime.utcnow().date().isoformat()
    if db_user.query_date != today:
        db_user.query_count_today = 0
        db_user.query_date        = today
    # 每日提问次数限制（测试期间暂停）
    # if db_user.plan == "free" and db_user.query_count_today >= FREE_QUERY_DAILY_LIMIT:
    #     raise HTTPException(403, f"今日提问次数已达上限（{FREE_QUERY_DAILY_LIMIT}次），请明天再来或升级专业版")
    db_user.query_count_today += 1
    session.commit()


class HistoryMsg(BaseModel):
    role:    str   # "user" | "assistant"
    content: str

class AskRequest(BaseModel):
    question:  str
    top_k:     int            = 5
    doc_ids:   list[str]      = []   # 空 = 全库；非空 = 指定文档
    section:   str            = ""   # 非空 = 只检索该章节
    task_hint: str            = ""   # 显式路由: qa / multi / review / writing / cite
    history:   list[HistoryMsg] = [] # 多轮对话历史
    use_web:   bool           = False # 联网检索
    extra_context: str        = ""   # 文本参考资料（直接粘贴，不经过 RAG）


@router.post("/ask")
def ask_question(req: AskRequest, session: Session = Depends(get_session),
                 current_user: User = Depends(get_current_user)):
    require_verified(current_user)
    if not req.question.strip():
        return {"error": "问题不能为空"}
    _check_and_count_query(current_user, session)
    answer, sources = ask(
        req.question,
        doc_ids       = req.doc_ids or None,
        section       = req.section or None,
        task_hint     = req.task_hint,
        history       = [{"role": m.role, "content": m.content} for m in req.history],
        use_web       = req.use_web,
        extra_context = req.extra_context,
        plan          = effective_plan(current_user),
    )
    return {"question": req.question, "answer": answer, "sources": sources}


@router.post("/ask/stream")
def ask_stream(req: AskRequest, session: Session = Depends(get_session),
               current_user: User = Depends(get_current_user)):
    require_verified(current_user)
    if not req.question.strip():
        return {"error": "问题不能为空"}
    _check_and_count_query(current_user, session)

    doc_ids   = req.doc_ids or None
    section   = req.section or None
    task_hint = req.task_hint
    history   = [{"role": m.role, "content": m.content} for m in req.history]
    use_web   = req.use_web

    plan = effective_plan(current_user)

    def event_generator():
        try:
            gen = ask(req.question, stream=True,
                      doc_ids=doc_ids, section=section, task_hint=task_hint,
                      history=history, use_web=use_web,
                      extra_context=req.extra_context,
                      plan=plan)
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


@router.get("/documents/{doc_id}/cite")
def get_citation(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    doc = session.query(Document).filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    _fallback = {
        "title": doc.original_name, "authors": [], "journal": "",
        "year": "", "volume": "", "issue": "", "pages": "",
        "doi": "", "publisher": "", "location": "", "type": "J",
        "filename": doc.original_name,
    }

    chunks = get_doc_header(doc_id, n=6)
    if not chunks:
        return _fallback

    header_text = "\n\n".join(c.get("text", "") for c in chunks)[:3500]

    try:
        import openai as _oa
        import re as _re
        _key, _base, _model = MODEL_ROUTES["cite"]
        client = _oa.OpenAI(api_key=_key, base_url=_base)
        resp = client.chat.completions.create(
            model=_model,
            messages=[{"role": "user", "content": (
                "从以下学术文献首页内容中提取文献元数据，以严格JSON格式返回（无法提取的字段用空字符串，"
                "authors 为作者姓名的字符串数组）。\n\n"
                f"---\n{header_text}\n---\n\n"
                '只返回JSON，无其他内容：\n'
                '{"title":"","authors":[],"journal":"","year":"","volume":"",'
                '"issue":"","pages":"","doi":"","publisher":"","location":"","type":"J"}\n\n'
                "type: J=期刊论文 B=书籍 C=会议论文 D=学位论文"
            )}],
            temperature=0.1,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
        # 兼容带/不带 ```json ``` 包裹的输出
        m = _re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
        if m:
            raw = m.group(1)
        meta = json.loads(raw.strip())
        meta["filename"] = doc.original_name
        return meta
    except Exception as e:
        print(f"[cite] 元数据提取失败: {e}")
        _fallback["_note"] = f"元数据提取失败：{e}"
        return _fallback
