"""
用户认证接口
POST /api/auth/register        → 注册（发送验证邮件）
POST /api/auth/login           → 登录
GET  /api/auth/me              → 当前用户信息
GET  /api/auth/verify-email    → 邮箱验证（链接跳转）
POST /api/auth/resend-verify   → 重新发送验证邮件
POST /api/auth/admin/activate  → 管理员激活套餐
"""
import hashlib, os, secrets, uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
import jwt

from db.database import get_session, User
from config import SECRET_KEY, ADMIN_SECRET, JWT_EXPIRE_DAYS, APP_URL, EMAIL_VERIFY_HOURS
from services.email_sender import send_verify_email

router   = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()


# ── 密码 ──────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt   = os.urandom(32).hex()
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}:{hashed}"

def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hashed = stored.split(":", 1)
        return hashlib.sha256((password + salt).encode()).hexdigest() == hashed
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────
def create_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "令牌已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效令牌")


# ── 认证依赖 ──────────────────────────────────────────────────
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: Session = Depends(get_session),
) -> User:
    user_id = decode_token(credentials.credentials)
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def effective_plan(user: User) -> str:
    """计算实际生效的套餐（admin→pro；付费套餐到期→free）。"""
    if user.is_admin:
        return "pro"
    if user.plan in ("pro", "plus", "enterprise") and user.plan_expires_at and user.plan_expires_at < datetime.utcnow():
        return "free"
    return user.plan or "free"


# ── 请求体 ────────────────────────────────────────────────────
class AuthReq(BaseModel):
    email: str
    password: str

class ResendReq(BaseModel):
    email: str

class ActivateReq(BaseModel):
    email: str
    plan: str  = "pro"
    days: int  = 30
    admin_secret: str

class ForgotPasswordReq(BaseModel):
    email: str

class ResetPasswordReq(BaseModel):
    token: str
    password: str


def _gen_verify_token() -> tuple[str, datetime]:
    """返回 (token, expires_at)"""
    token      = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=EMAIL_VERIFY_HOURS)
    return token, expires_at


# ── 接口 ──────────────────────────────────────────────────────
@router.post("/register")
def register(req: AuthReq, session: Session = Depends(get_session)):
    if "@" not in req.email or len(req.email) < 5:
        raise HTTPException(400, "请输入有效邮箱")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少6位")
    if session.query(User).filter_by(email=req.email.lower()).first():
        raise HTTPException(400, "该邮箱已注册")

    token, expires_at = _gen_verify_token()
    user = User(
        id                      = str(uuid.uuid4()),
        email                   = req.email.lower(),
        password_hash           = hash_password(req.password),
        email_verified          = False,
        email_verify_token      = token,
        email_verify_expires_at = expires_at,
    )
    session.add(user)
    session.commit()

    send_verify_email(user.email, token)

    return {
        "message":       "注册成功，验证邮件已发送，请检查收件箱（含垃圾邮件）",
        "email":         user.email,
        "email_verified": False,
    }


@router.post("/login")
def login(req: AuthReq, session: Session = Depends(get_session)):
    user = session.query(User).filter_by(email=req.email.lower()).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")

    if not user.email_verified:
        # 登录成功但未验证：返回 token + 标记，前端展示验证提示
        return {
            "token":         create_token(user.id),
            "email":         user.email,
            "plan":          user.plan,
            "email_verified": False,
            "pdf_count":     user.pdf_count,
            "query_count_today": user.query_count_today,
        }

    today = datetime.utcnow().date().isoformat()
    if user.query_date != today:
        user.query_count_today = 0
        user.query_date        = today
        session.commit()

    return {
        "token":         create_token(user.id),
        "email":         user.email,
        "plan":          user.plan,
        "email_verified": True,
        "pdf_count":     user.pdf_count,
        "query_count_today": user.query_count_today,
    }


@router.get("/verify-email")
def verify_email(token: str, session: Session = Depends(get_session)):
    user = session.query(User).filter_by(email_verify_token=token).first()
    _base = APP_URL.rstrip("/")
    if not user:
        return RedirectResponse(url=f"{_base}/app?verify=invalid")
    if user.email_verified:
        return RedirectResponse(url=f"{_base}/app?verify=already")
    if user.email_verify_expires_at and user.email_verify_expires_at < datetime.utcnow():
        return RedirectResponse(url=f"{_base}/app?verify=expired&email={user.email}")

    user.email_verified          = True
    user.email_verify_token      = None
    user.email_verify_expires_at = None
    session.commit()
    return RedirectResponse(url=f"{_base}/app?verify=ok")


@router.post("/resend-verify")
def resend_verify(req: ResendReq, session: Session = Depends(get_session)):
    user = session.query(User).filter_by(email=req.email.lower()).first()
    if not user:
        raise HTTPException(404, "邮箱未注册")
    if user.email_verified:
        raise HTTPException(400, "该邮箱已完成验证")

    token, expires_at = _gen_verify_token()
    user.email_verify_token      = token
    user.email_verify_expires_at = expires_at
    session.commit()

    send_verify_email(user.email, token)
    return {"message": "验证邮件已重新发送，请检查收件箱（含垃圾邮件）"}


@router.get("/me")
def me(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    today = datetime.utcnow().date().isoformat()
    db_user = session.query(User).filter_by(id=user.id).first()
    if db_user.query_date != today:
        db_user.query_count_today = 0
        db_user.query_date        = today
        session.commit()
    return {
        "email":             db_user.email,
        "plan":              effective_plan(db_user),   # 返回实际生效套餐（到期自动降级）
        "plan_raw":          db_user.plan,              # 原始 DB 值（前端展示到期提示用）
        "plan_expires_at":   str(db_user.plan_expires_at) if db_user.plan_expires_at else None,
        "pdf_count":         db_user.pdf_count,
        "query_count_today": db_user.query_count_today,
        "email_verified":    bool(db_user.email_verified),
    }


@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordReq, session: Session = Depends(get_session)):
    """申请密码重置邮件（无论邮箱是否存在，都返回 200 防止枚举）。"""
    user = session.query(User).filter_by(email=req.email.lower().strip()).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token            = token
        user.reset_token_expires_at = datetime.utcnow() + timedelta(hours=1)
        session.commit()
        from services.email_sender import send_reset_email
        send_reset_email(user.email, token)
    return {"message": "如果该邮箱已注册，重置链接已发送至邮箱，请检查收件箱（含垃圾邮件）"}


@router.post("/reset-password")
def reset_password(req: ResetPasswordReq, session: Session = Depends(get_session)):
    user = session.query(User).filter_by(reset_token=req.token).first()
    if not user or not user.reset_token_expires_at:
        raise HTTPException(400, "重置链接无效或已过期，请重新申请")
    if user.reset_token_expires_at < datetime.utcnow():
        raise HTTPException(400, "重置链接已过期（1小时有效），请重新申请")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少6位")
    user.password_hash          = hash_password(req.password)
    user.reset_token            = None
    user.reset_token_expires_at = None
    session.commit()
    return {"message": "密码重置成功，请用新密码登录"}


@router.post("/admin/activate")
def activate(req: ActivateReq, session: Session = Depends(get_session)):
    if req.admin_secret != ADMIN_SECRET:
        raise HTTPException(403, "管理员密码错误")
    user = session.query(User).filter_by(email=req.email.lower()).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.plan            = req.plan
    user.plan_expires_at = datetime.utcnow() + timedelta(days=req.days)
    session.commit()
    return {"message": f"已激活 {user.email} 的 {req.plan} 套餐，有效期 {req.days} 天"}
