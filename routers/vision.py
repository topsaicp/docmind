"""
图像分析接口
POST /api/analyze-image  → 发送 base64 图片给 Gemini，返回分析结果（流式）
"""
import json, base64
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI

from config import GEMINI_API_KEY, GEMINI_BASE_URL
from routers.auth import get_current_user
from db.database import User

router = APIRouter(prefix="/api", tags=["vision"])

_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise HTTPException(503, "视觉分析功能未配置（缺少 GEMINI_API_KEY）")
        _client = OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL)
    return _client


class ImageAnalyzeRequest(BaseModel):
    image_b64: str        # base64 编码的图片（不含 data:image/... 前缀）
    image_type: str = "image/png"   # MIME 类型
    question: str  = "请详细分析这张图片的内容，提炼关键信息。"
    context: str   = ""   # 可选：当前文档名或上下文提示


@router.post("/analyze-image/stream")
def analyze_image_stream(
    req: ImageAnalyzeRequest,
    current_user: User = Depends(get_current_user),
):
    client = _get_client()

    system_prompt = "你是一个学术文献分析助手，擅长解读论文截图、图表、公式和表格。"
    if req.context:
        system_prompt += f"\n当前文档：{req.context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{req.image_type};base64,{req.image_b64}"
                    },
                },
                {"type": "text", "text": req.question},
            ],
        },
    ]

    def event_gen():
        try:
            resp = client.chat.completions.create(
                model="gemini-1.5-flash",
                messages=messages,
                max_tokens=2048,
                stream=True,
            )
            for chunk in resp:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'type':'text','text':delta}, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = str(e)
            yield f"data: {json.dumps({'type':'text','text':f'❌ 图像分析失败：{err}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
