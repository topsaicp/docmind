"""
语言优化工具接口
POST /api/reduce         — 文本语言优化（SSE 流式）
POST /api/reduce/upload  — 文件上传后语言优化（SSE 流式）
"""
import io, json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.database import User
from routers.auth import get_current_user, effective_plan
from services.text_reducer import reduce_stream
from config import get_limits, PLAN_LIMITS

router = APIRouter(prefix="/api", tags=["reduce"])


def require_verified(user: User):
    if not user.email_verified:
        raise HTTPException(403, "请先验证邮箱后再使用此功能")


def _check_word_limit(text: str, plan: str):
    wc    = len(text.split())
    limit = get_limits(plan)["reduce_max_words"]
    if wc > limit:
        if plan in ("pro", "enterprise"):
            hint = ""
        elif plan == "plus":
            hint = f" 升级专业版可处理最多 {PLAN_LIMITS['pro']['reduce_max_words']} 词。"
        else:
            hint = f" 升级基础版可处理 {PLAN_LIMITS['plus']['reduce_max_words']} 词，升级专业版可处理 {PLAN_LIMITS['pro']['reduce_max_words']} 词。"
        raise HTTPException(400, f"文本字数超出当前套餐上限（当前 {wc} 词，上限 {limit} 词）。{hint}")


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
    require_verified(current_user)
    plan = effective_plan(current_user)
    if plan not in ("plus", "pro", "enterprise"):
        raise HTTPException(403, "语言优化功能需要基础版或以上套餐，升级后即可使用")
    if not req.text.strip():
        raise HTTPException(400, "文本不能为空")
    if req.mode not in ("ai", "dup", "both"):
        raise HTTPException(400, "无效的语言优化模式")
    # 基础版仅支持表达优化，深度改写为专业版专属
    if plan == "plus" and req.mode in ("dup", "both"):
        raise HTTPException(403, "深度改写为专业版专属功能，升级后可使用完整优化")
    _check_word_limit(req.text, plan)
    return StreamingResponse(_gen(req.text, req.mode), media_type="text/event-stream")


@router.post("/reduce/upload")
async def reduce_upload(
    file: UploadFile = File(...),
    mode: str = Form("ai"),
    current_user: User = Depends(get_current_user),
):
    require_verified(current_user)
    plan = effective_plan(current_user)
    if plan not in ("plus", "pro", "enterprise"):
        raise HTTPException(403, "语言优化功能需要基础版或以上套餐，升级后即可使用")
    if mode not in ("ai", "dup", "both"):
        raise HTTPException(400, "无效的语言优化模式")

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
    if plan == "plus" and mode in ("dup", "both"):
        raise HTTPException(403, "深度改写为专业版专属功能，升级后可使用完整优化")
    _check_word_limit(text, plan)
    return StreamingResponse(_gen(text, mode), media_type="text/event-stream")
