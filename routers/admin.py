"""
管理后台 API（全部需要 is_admin=True）
GET  /api/admin/stats
GET  /api/admin/users            ?q=email&page=1&limit=20
GET  /api/admin/users/{id}
POST /api/admin/users/{id}/plan      body: {plan, days}
POST /api/admin/users/{id}/password  body: {password}
DELETE /api/admin/users/{id}
GET  /api/admin/orders           ?page=1&limit=20&status=
PATCH /api/admin/leads/{id}      body: {status}
GET  /api/admin/codes            ?page=1&limit=50&status=
PATCH /api/admin/codes/{id}/void
GET  /api/admin/settings/plans
PUT  /api/admin/settings/plans
GET  /api/admin/settings/site
PUT  /api/admin/settings/site
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from db.database import get_session, User, Document, SiteVisit
from payment.models import PayOrder, ActivationCode, Lead
from payment.service import activate, gen_order_no, PLAN_NAMES
from routers.auth import get_current_user, hash_password
from services import settings_service
from services.embedder import delete_doc
from config import UPLOAD_DIR

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "无权限")
    return user


# ── 仪表盘统计 ─────────────────────────────────────────────────────────────
@router.get("/stats")
def admin_stats(db: Session = Depends(get_session), _: User = Depends(require_admin)):
    today = datetime.utcnow().date().isoformat()
    total_users  = db.query(func.count(User.id)).scalar() or 0
    new_today    = db.query(func.count(User.id)).filter(
        func.date(User.created_at) == today
    ).scalar() or 0
    paid_users   = db.query(func.count(User.id)).filter(
        User.plan.in_(["plus", "pro", "custom", "enterprise"]),
        or_(User.plan_expires_at.is_(None), User.plan_expires_at > datetime.utcnow()),
    ).scalar() or 0
    orders_today = db.query(func.count(PayOrder.id)).filter(
        func.date(PayOrder.paid_at) == today,
        PayOrder.status == "paid",
    ).scalar() or 0
    revenue_today = db.query(func.sum(PayOrder.amount)).filter(
        func.date(PayOrder.paid_at) == today,
        PayOrder.status == "paid",
    ).scalar() or 0
    visits_today = db.query(func.count(SiteVisit.id)).filter(
        func.date(SiteVisit.created_at) == today,
    ).scalar() or 0
    visitors_today = db.query(func.count(func.distinct(SiteVisit.ip))).filter(
        func.date(SiteVisit.created_at) == today,
    ).scalar() or 0

    recent_orders = (
        db.query(PayOrder)
        .filter(PayOrder.status == "paid")
        .order_by(PayOrder.paid_at.desc())
        .limit(8)
        .all()
    )
    orders_list = []
    for o in recent_orders:
        u = db.query(User).filter(User.id == o.user_id).first()
        orders_list.append({
            "order_no":  o.order_no,
            "email":     u.email if u else o.user_id,
            "plan":      PLAN_NAMES.get(o.plan, o.plan),
            "amount":    str(o.amount),
            "channel":   o.channel,
            "paid_at":   o.paid_at.isoformat() if o.paid_at else None,
        })

    return {
        "total_users":    total_users,
        "new_today":      new_today,
        "paid_users":     paid_users,
        "orders_today":   orders_today,
        "revenue_today":  float(revenue_today),
        "visits_today":   visits_today,
        "visitors_today": visitors_today,
        "recent_orders":  orders_list,
    }


# ── 用户管理 ──────────────────────────────────────────────────────────────
@router.get("/users")
def admin_users(
    q: str = "", page: int = 1, limit: int = 20,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    query = db.query(User)
    if q.strip():
        query = query.filter(User.email.ilike(f"%{q.strip()}%"))
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "total": total,
        "page":  page,
        "items": [_user_row(u) for u in users],
    }


@router.get("/users/{user_id}")
def admin_get_user(
    user_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "用户不存在")
    return _user_row(u)


def _user_row(u: User) -> dict:
    return {
        "id":                u.id,
        "email":             u.email,
        "plan":              u.plan,
        "plan_name":         PLAN_NAMES.get(u.plan, u.plan or "免费版"),
        "plan_expires_at":   u.plan_expires_at.isoformat() if u.plan_expires_at else None,
        "pdf_count":         u.pdf_count,
        "query_count_today": u.query_count_today,
        "is_admin":          bool(u.is_admin),
        "email_verified":    bool(u.email_verified),
        "created_at":        u.created_at.isoformat() if u.created_at else None,
    }


class PlanGrantIn(BaseModel):
    plan: str
    days: int = 30


@router.post("/users/{user_id}/plan")
def admin_set_user_plan(
    user_id: str,
    body: PlanGrantIn,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    if body.plan not in ("free", "plus", "pro", "custom", "enterprise"):
        raise HTTPException(400, "套餐无效")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    activate(db, user_id, body.plan, body.days)
    db.add(PayOrder(
        order_no=gen_order_no(), user_id=user_id, plan=body.plan,
        days=body.days, amount=0, channel="manual",
        status="paid", paid_at=datetime.utcnow(),
    ))
    db.commit()
    db.refresh(target)
    return {"ok": True, **_user_row(target)}


class SetPasswordIn(BaseModel):
    password: str


@router.post("/users/{user_id}/password")
def admin_set_password(
    user_id: str,
    body: SetPasswordIn,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少6位")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    target.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: str,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "不能删除自己的账户")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.is_admin:
        raise HTTPException(400, "不能删除管理员账户")

    docs = db.query(Document).filter(Document.user_id == user_id).all()
    for doc in docs:
        delete_doc(doc.id)
        file_path = UPLOAD_DIR / doc.filename
        if file_path.exists():
            file_path.unlink()
        db.delete(doc)

    db.delete(target)
    db.commit()
    return {"ok": True}


# ── 订单列表 ──────────────────────────────────────────────────────────────
@router.get("/orders")
def admin_orders(
    page: int = 1, limit: int = 20, status: str = "",
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    query = db.query(PayOrder)
    if status:
        query = query.filter(PayOrder.status == status)
    total  = query.count()
    orders = query.order_by(PayOrder.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    rows = []
    for o in orders:
        u = db.query(User).filter(User.id == o.user_id).first()
        rows.append({
            "order_no":  o.order_no,
            "email":     u.email if u else o.user_id,
            "plan":      o.plan,
            "plan_name": PLAN_NAMES.get(o.plan, o.plan),
            "amount":    str(o.amount),
            "channel":   o.channel,
            "status":    o.status,
            "trade_no":  o.trade_no,
            "paid_at":   o.paid_at.isoformat() if o.paid_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })
    return {"total": total, "page": page, "items": rows}


# ── 咨询线索 ──────────────────────────────────────────────────────────────
class LeadStatusIn(BaseModel):
    status: str  # new / contacted / won / lost


@router.patch("/leads/{lead_id}")
def admin_update_lead(
    lead_id: str,
    body: LeadStatusIn,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    if body.status not in ("new", "contacted", "won", "lost"):
        raise HTTPException(400, "无效状态")
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "线索不存在")
    lead.status = body.status
    db.commit()
    return {"ok": True, "id": lead_id, "status": body.status}


# ── 激活码管理 ────────────────────────────────────────────────────────────
@router.get("/codes")
def admin_list_codes(
    page: int = 1, limit: int = 50, status: str = "",
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    query = db.query(ActivationCode)
    if status:
        query = query.filter(ActivationCode.status == status)
    total = query.count()
    codes = query.order_by(ActivationCode.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    rows = []
    for c in codes:
        used_email = None
        if c.used_by:
            u = db.query(User).filter(User.id == c.used_by).first()
            used_email = u.email if u else c.used_by
        rows.append({
            "id":         c.id,
            "code":       c.code,
            "plan":       c.plan,
            "plan_name":  PLAN_NAMES.get(c.plan, c.plan),
            "days":       c.days,
            "status":     c.status,
            "note":       c.note,
            "used_email": used_email,
            "used_at":    c.used_at.isoformat() if c.used_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return {"total": total, "page": page, "items": rows}


@router.patch("/codes/{code_id}/void")
def admin_void_code(
    code_id: str,
    db: Session = Depends(get_session),
    _: User = Depends(require_admin),
):
    code = db.query(ActivationCode).filter(ActivationCode.id == code_id).first()
    if not code:
        raise HTTPException(404, "激活码不存在")
    if code.status == "used":
        raise HTTPException(400, "已使用的激活码无法作废")
    code.status = "void"
    db.commit()
    return {"ok": True}


# ── 套餐配置 ──────────────────────────────────────────────────────────────
@router.get("/settings/plans")
def admin_get_plans(_: User = Depends(require_admin)):
    return settings_service.get_plans()


@router.put("/settings/plans")
def admin_save_plans(data: dict, _: User = Depends(require_admin)):
    required = {"free", "plus", "pro", "custom"}
    if not required.issubset(data.keys()):
        raise HTTPException(400, f"套餐配置必须包含: {required}")
    settings_service.save_plans(data)
    return {"ok": True}


# ── 站点配置 ──────────────────────────────────────────────────────────────
@router.get("/settings/site")
def admin_get_site(_: User = Depends(require_admin)):
    return settings_service.get_site()


@router.put("/settings/site")
def admin_save_site(data: dict, _: User = Depends(require_admin)):
    settings_service.save_site(data)
    return {"ok": True}
