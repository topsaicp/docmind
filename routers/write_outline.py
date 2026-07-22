"""
论文/综述目录生成
POST /api/write/outline → AI 根据主题+文献生成三级目录草稿（非流式，返回 JSON）
"""
import json, re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from db.database import User
from routers.auth import get_current_user, effective_plan
from services.retriever import retrieve_per_doc, build_multi_doc_context
from config import MODEL_ROUTES

router = APIRouter(prefix="/api/write", tags=["write"])

_client: OpenAI | None = None
_model: str = ""


def _get_client() -> tuple[OpenAI, str]:
    global _client, _model
    if _client is None:
        key, base, model = MODEL_ROUTES["review"]
        _client = OpenAI(api_key=key, base_url=base)
        _model = model
    return _client, _model


class OutlineRequest(BaseModel):
    topic: str = ""
    doc_ids: list[str] = []


@router.post("/outline")
def generate_outline(
    req: OutlineRequest,
    current_user: User = Depends(get_current_user),
):
    if not current_user.email_verified:
        raise HTTPException(403, "请先验证邮箱后再使用此功能")
    plan = effective_plan(current_user)
    if plan == "free":
        raise HTTPException(403, "论文写作功能需要基础版或专业版套餐，升级后即可使用")

    query = req.topic.strip() or "该研究领域"
    context = ""
    if req.doc_ids:
        per_doc = retrieve_per_doc(query, req.doc_ids, chunks_per_doc=3)
        context, _ = build_multi_doc_context(per_doc)

    client, model = _get_client()
    ref_block = f"参考文献内容片段：\n{context}\n" if context else "（未提供参考文献片段，请按通用学术论文结构设计）\n"
    prompt = (
        "你是学术论文写作顾问。请根据以下信息，为一篇学术论文设计目录结构（三级）：\n\n"
        f"论文主题：{query}\n{ref_block}\n"
        "要求：\n"
        "1. 一级章节 5~7 个（如「引言」「文献综述」「研究方法」「结果与分析」「讨论与结论」"
        "「参考文献」，可根据主题调整措辞和数量，务必包含参考文献一章）\n"
        "2. 每个一级章节下设 2~4 个二级小节\n"
        "3. 每个二级小节下设 1~3 个三级写作要点\n"
        "4. 严格按以下 JSON 数组格式输出，不要输出任何 JSON 之外的文字，不要用 markdown 代码块包裹：\n"
        '[{"label":"一、引言","hint":"该章节的一句话写作提示","children":'
        '[{"label":"1.1 研究背景与意义","points":["要点1","要点2"]}]}]'
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
        outline = json.loads(raw)
        if not isinstance(outline, list) or not outline:
            raise ValueError("返回内容不是有效的目录列表")
    except Exception as e:
        raise HTTPException(502, f"目录生成失败：{e}")

    return {"outline": outline}
