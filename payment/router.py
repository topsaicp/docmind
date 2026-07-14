"""
支付与尊享版路由
POST /api/pay/orders            下单 → 返回支付宝收银台链接
POST /api/pay/alipay/notify     支付宝异步回调（验签 + 幂等 + 开通）
GET  /api/pay/orders/{no}       查订单状态（前端回跳后轮询一次）
POST /api/pay/orders/{no}/sync  主动查单兜底（回调丢失时救单）
POST /api/pay/redeem            激活码兑换
GET  /api/pay/membership        当前会员状态
POST /api/leads                 专属尊享版咨询提交
POST /api/admin/grant           管理员手动开通（人工成交用）
POST /api/admin/codes           管理员批量生成激活码
GET  /api/admin/leads           管理员查看咨询线索
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.database import get_session, User
from routers.auth import get_current_user
from . import alipay_client
from .models import PayOrder, Lead
from .service import (PLANS, PLAN_NAMES, PayError, activate, gen_codes,
                      gen_order_no, membership_info, redeem_code)

logger = logging.getLogger("payment")
router = APIRouter(prefix="/api", tags=["payment"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "无权限")
    return user


# ══════════════════════ 支付 ══════════════════════
class CreateOrderIn(BaseModel):
    plan:   str            # plus | pro
    mobile: bool = False   # 手机端 True


@router.post("/pay/orders")
def create_order(body: CreateOrderIn,
                 session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    if not user.email_verified:
        raise HTTPException(403, "请先验证邮箱后再订阅")
    cfg = PLANS.get(body.plan)
    if not cfg:
        raise HTTPException(400, "该套餐不支持在线订阅")

    order = PayOrder(
        order_no=gen_order_no(), user_id=user.id, plan=body.plan,
        days=cfg["days"], amount=Decimal(cfg["amount"]),
        channel="alipay", status="pending",
    )
    session.add(order)
    session.commit()

    try:
        url = alipay_client.pay_url(
            order_no=order.order_no,
            amount=cfg["amount"],
            subject=f"DocMind {cfg['name']} {cfg['days']}天",
            mobile=body.mobile,
        )
    except Exception as e:
        logger.exception("alipay pay_url failed")
        raise HTTPException(502, f"支付下单失败：{e}")

    return {"order_no": order.order_no, "pay_url": url, "amount": cfg["amount"]}


@router.post("/pay/alipay/notify")
async def alipay_notify(request: Request, session: Session = Depends(get_session)):
    """支付宝异步通知。必须返回纯文本 success，否则支付宝重试。"""
    form = dict(await request.form())
    sign = form.pop("sign", None)
    form.pop("sign_type", None)

    if not sign or not alipay_client.verify_notify(form, sign):
        logger.warning("notify 验签失败 %s", form.get("out_trade_no"))
        raise HTTPException(400, "invalid signature")

    if form.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return PlainTextResponse("success")

    order_no = form.get("out_trade_no")
    order = (session.query(PayOrder)
                    .filter(PayOrder.order_no == order_no)
                    .with_for_update()
                    .first())
    if order is None:
        logger.error("notify 未知订单 %s", order_no)
        return PlainTextResponse("success")

    if Decimal(form.get("total_amount", "0")) != Decimal(order.amount):
        logger.error("notify 金额不符 %s: %s != %s", order_no,
                     form.get("total_amount"), order.amount)
        raise HTTPException(400, "amount mismatch")

    if order.status == "paid":          # 幂等
        session.rollback()
        return PlainTextResponse("success")

    order.status   = "paid"
    order.paid_at  = datetime.utcnow()
    order.trade_no = form.get("trade_no")
    activate(session, order.user_id, order.plan, order.days)
    session.commit()
    logger.info("支付成功并开通 order=%s user=%s plan=%s",
                order_no, order.user_id, order.plan)
    return PlainTextResponse("success")


@router.get("/pay/orders/{order_no}")
def order_status(order_no: str,
                 session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    order = session.query(PayOrder).filter(PayOrder.order_no == order_no).first()
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    return {"order_no": order_no, "status": order.status}


@router.post("/pay/orders/{order_no}/sync")
def order_sync(order_no: str,
               session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    """回调丢失时的兜底：主动向支付宝核实。"""
    order = (session.query(PayOrder)
                    .filter(PayOrder.order_no == order_no)
                    .with_for_update()
                    .first())
    if order is None or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    if order.status == "paid":
        return {"order_no": order_no, "status": "paid"}

    try:
        resp = alipay_client.query(order_no)
    except Exception as e:
        session.rollback()
        raise HTTPException(502, f"查询失败：{e}")

    if (resp.get("code") == "10000"
            and resp.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED")
            and Decimal(resp.get("total_amount", "0")) == Decimal(order.amount)):
        order.status   = "paid"
        order.paid_at  = datetime.utcnow()
        order.trade_no = resp.get("trade_no")
        activate(session, order.user_id, order.plan, order.days)
        session.commit()
        return {"order_no": order_no, "status": "paid"}

    session.rollback()
    return {"order_no": order_no, "status": order.status}


class RedeemIn(BaseModel):
    code: str


@router.post("/pay/redeem")
def redeem(body: RedeemIn,
           session: Session = Depends(get_session),
           user: User = Depends(get_current_user)):
    try:
        return {"ok": True, **redeem_code(session, user.id, body.code)}
    except PayError as e:
        raise HTTPException(400, str(e))


@router.get("/pay/membership")
def membership(user: User = Depends(get_current_user)):
    return membership_info(user)


# ══════════════════════ 专属尊享版咨询 ══════════════════════
class LeadIn(BaseModel):
    email:   str
    contact: str = ""
    need:    str = ""


@router.post("/leads")
def create_lead(body: LeadIn, request: Request,
                session: Session = Depends(get_session)):
    if not body.email.strip():
        raise HTTPException(400, "请留下邮箱")
    uid = None
    try:  # 已登录则关联，未登录也允许提交
        from routers.auth import get_current_user as _g
        uid = None
    except Exception:
        pass

    lead = Lead(user_id=uid, email=body.email.strip(),
                contact=body.contact.strip(), need=body.need.strip()[:2000])
    session.add(lead)
    session.commit()

    # 邮件通知管理员（失败不影响提交）
    try:
        from services.email_sender import send_notify
        send_notify(
            to_email="topsaitech@163.com",
            subject="【DocMind】专属尊享版新咨询",
            html=f"<h3>收到一条新的专属尊享版咨询</h3>"
                 f"<p><b>邮箱：</b>{lead.email}</p>"
                 f"<p><b>联系方式：</b>{lead.contact or '未填写'}</p>"
                 f"<p><b>需求描述：</b><br>{(lead.need or '未填写').replace(chr(10), '<br>')}</p>"
                 f"<p><b>提交时间：</b>{lead.created_at}</p>",
        )
    except Exception as e:
        logger.warning("lead 通知邮件发送失败: %s", e)

    return {"ok": True}


# ══════════════════════ 管理员 ══════════════════════
class GrantIn(BaseModel):
    email: str
    plan:  str            # plus | pro | custom | free
    days:  int = 30


@router.post("/admin/grant")
def admin_grant(body: GrantIn,
                session: Session = Depends(get_session),
                admin: User = Depends(require_admin)):
    """人工成交后手动开通。也可用于赠送、补偿、降级。"""
    target = session.query(User).filter(User.email == body.email.strip()).first()
    if target is None:
        raise HTTPException(404, "用户不存在（请让对方先注册）")
    if body.plan not in ("free", "plus", "pro", "custom"):
        raise HTTPException(400, "套餐无效")

    activate(session, target.id, body.plan, body.days)
    session.add(PayOrder(
        order_no=gen_order_no(), user_id=target.id, plan=body.plan,
        days=body.days, amount=0, channel="manual",
        status="paid", paid_at=datetime.utcnow(),
    ))
    session.commit()
    session.refresh(target)
    return {"ok": True, "email": target.email, "plan": target.plan,
            "plan_name": PLAN_NAMES.get(target.plan, target.plan),
            "expires_at": target.plan_expires_at.isoformat() if target.plan_expires_at else None}


class CodesIn(BaseModel):
    plan:  str
    days:  int = 30
    count: int = 10
    note:  str = ""


@router.post("/admin/codes")
def admin_codes(body: CodesIn,
                session: Session = Depends(get_session),
                admin: User = Depends(require_admin)):
    if body.count < 1 or body.count > 500:
        raise HTTPException(400, "数量需在 1-500 之间")
    codes = gen_codes(session, body.plan, body.days, body.count, body.note)
    return {"ok": True, "count": len(codes), "codes": codes}


@router.get("/admin/leads")
def admin_leads(session: Session = Depends(get_session),
                admin: User = Depends(require_admin)):
    rows = session.query(Lead).order_by(Lead.created_at.desc()).limit(100).all()
    return [{"id": r.id, "email": r.email, "contact": r.contact, "need": r.need,
             "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]
