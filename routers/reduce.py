"""
降率工具接口
POST /api/reduce         — 文本降率（SSE 流式）
POST /api/reduce/upload  — 文件上传后降率（SSE 流式）
"""
import io, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.database import User
from routers.auth import get_current_user
from services.text_reducer import reduce_stream
from config import get_limits

router = APIRouter(prefix="/api", tags=["reduce"])


def _check_word_limit(text: str, plan: str):
    wc    = len(text.split())
    limit = get_limits(plan)["reduce_max_words"]
    if wc > limit:
        plan_label = "专业版" if plan == "pro" else "免费版"
        raise HTTPException(
            400,
            f"文本字数超出{plan_label}上限（当前 {wc} 词，上限 {limit} 词）。"
            + ("" if plan == "pro" else "升级专业版可提升至 10,000 词。"),
        )


class ReduceRequest(BaseModel):
    text: str
    mode: str   # ai | dup | both


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _gen(text: str, mode: str):
    for ev in reduce_stream(text, mode):
        yield _sse(ev)
        if ev["type"] in ("done", "error"):
            break
    yield "data: [DONE]\n\n"


@router.post("/reduce")
def reduce_text(
    req: ReduceRequest,
    current_user: User = Depends(get_current_user),
):
    from routers.query import effective_plan
    if not req.text.strip():
        raise HTTPException(400, "文本不能为空")
    if req.mode not in ("ai", "dup", "both"):
        raise HTTPException(400, "无效的降率模式")
    plan = effective_plan(current_user)
    _check_word_limit(req.text, plan)
    return StreamingResponse(_gen(req.text, req.mode), media_type="text/event-stream")


@router.post("/reduce/upload")
async def reduce_upload(
    file: UploadFile = File(...),
    mode: str = Form("ai"),
    current_user: User = Depends(get_current_user),
):
    if mode not in ("ai", "dup", "both"):
        raise HTTPException(400, "无效的降率模式")

    name = file.filename or ""
    data = await file.read()

    if name.lower().endswith(".txt"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="replace")

    elif name.lower().endswith(".docx"):
        try:
            from docx import Document
            doc  = Document(io.BytesIO(data))
            text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            raise HTTPException(400, f"docx 解析失败: {e}")

    else:
        raise HTTPException(400, "仅支持 .txt 和 .docx 格式")

    if not text.strip():
        raise HTTPException(400, "文件内容为空")

    from routers.query import effective_plan
    _check_word_limit(text, effective_plan(current_user))
    return StreamingResponse(_gen(text, mode), media_type="text/event-stream")
