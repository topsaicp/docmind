"""
支付接口（预留）
GET  /api/payment/plans   → 套餐信息
POST /api/payment/notify  → 支付回调（支付宝/微信接入后实现）
"""
from fastapi import APIRouter, Request
from datetime import datetime

router = APIRouter(prefix="/api/payment", tags=["payment"])

PLANS = {
    "plus": {
        "id": "plus_monthly", "name": "基础版", "price": 9.9,
        "currency": "CNY", "period": "month", "days": 30,
    },
    "pro": {
        "id": "pro_monthly", "name": "专业版", "price": 19.9,
        "currency": "CNY", "period": "month", "days": 30,
    },
    "enterprise": {
        "id": "enterprise_monthly", "name": "机构版", "price": 199,
        "currency": "CNY", "period": "month", "days": 30,
    },
}

@router.get("/plans")
def get_plans():
    return {"plans": list(PLANS.values())}


@router.post("/notify")
async def payment_notify(request: Request):
    body = await request.body()
    print(f"[payment] notify received at {datetime.utcnow().isoformat()}: {body[:200]}")
    # TODO: 验证签名 → 查订单 → 调用 auth.activate 升级用户
    return {"code": "success"}
