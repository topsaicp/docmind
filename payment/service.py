"""
支付业务服务层
所有开通权益的路径（支付宝回调 / 激活码 / 管理员手动）最终都调 activate()
"""
import random
import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from db.database import User
from .models import ActivationCode, PayOrder

PLAN_NAMES = {
    "free":   "免费版",
    "plus":   "基础版",
    "pro":    "专业版",
    "custom": "专属尊享版",
}


class PayError(Exception):
    pass


# ── 统一开通接口 ──────────────────────────────────────────────────
def activate(db: Session, user_id: str, plan: str, days: int) -> User:
    """给用户开通/续期套餐。写入 users.plan 与 users.plan_expires_at。
    规则：未到期续费从原到期日顺延；已过期或首次开通从现在起算。
    调用方负责 commit。"""
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        raise PayError("用户不存在")

    now  = datetime.utcnow()
    base = now
    if user.plan_expires_at and user.plan_expires_at > now and user.plan == plan:
        base = user.plan_expires_at          # 同档续费：顺延
    user.plan            = plan
    user.plan_expires_at = base + timedelta(days=days)
    return user


def membership_info(user: User) -> dict:
    now = datetime.utcnow()
    active = bool(user.plan and user.plan != "free"
                  and user.plan_expires_at and user.plan_expires_at > now)
    return {
        "plan":       user.plan if active else "free",
        "plan_name":  PLAN_NAMES.get(user.plan if active else "free", "免费版"),
        "expires_at": user.plan_expires_at.isoformat() if (active and user.plan_expires_at) else None,
        "active":     active,
    }


# ── 订单号：P + 时间戳 + 4位随机（仅字母数字，≤32 字符）────────────
def gen_order_no() -> str:
    ts   = datetime.now().strftime("%Y%m%d%H%M%S")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"P{ts}{rand}"


# ── 激活码 ────────────────────────────────────────────────────────
def gen_codes(db: Session, plan: str, days: int, count: int, note: str = "") -> list[str]:
    prefix = {"plus": "DMPLS", "pro": "DMPRO", "custom": "DMCUS"}.get(plan, "DMGEN")
    codes: list[str] = []
    for _ in range(count):
        raw  = secrets.token_hex(6).upper()
        code = f"{prefix}-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"
        db.add(ActivationCode(code=code, plan=plan, days=days, note=note))
        codes.append(code)
    db.commit()
    return codes


def redeem_code(db: Session, user_id: str, code: str) -> dict:
    code = code.strip().upper()
    ac = (db.query(ActivationCode)
            .filter(ActivationCode.code == code)
            .with_for_update()
            .first())
    if ac is None:
        raise PayError("激活码不存在，请检查输入")
    if ac.status == "used":
        raise PayError("该激活码已被使用")
    if ac.status == "void":
        raise PayError("该激活码已作废")

    ac.status  = "used"
    ac.used_by = user_id
    ac.used_at = datetime.utcnow()

    db.add(PayOrder(
        order_no=gen_order_no(), user_id=user_id, plan=ac.plan, days=ac.days,
        amount=0, channel="code", status="paid", paid_at=datetime.utcnow(),
    ))
    activate(db, user_id, ac.plan, ac.days)
    db.commit()
    return {"plan": ac.plan, "plan_name": PLAN_NAMES.get(ac.plan, ac.plan), "days": ac.days}
