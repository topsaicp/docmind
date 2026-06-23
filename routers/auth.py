"""
用户认证接口
POST /api/auth/register        → 注册
POST /api/auth/login           → 登录
GET  /api/auth/me              → 当前用户信息
POST /api/auth/admin/activate  → 管理员激活套餐
"""
import hashlib, os, uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
import jwt

from db.database import get_session, User
from config import SECRET_KEY, ADMIN_SECRET, JWT_EXPIRE_DAYS

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


# ── 请求体 ────────────────────────────────────────────────────
class AuthReq(BaseModel):
    email: str
    password: str

class ActivateReq(BaseModel):
    email: str
    plan: str  = "pro"
    days: int  = 30
    admin_secret: str


# ── 接口 ──────────────────────────────────────────────────────
@router.post("/register")
def register(req: AuthReq, session: Session = Depends(get_session)):
    if "@" not in req.email or len(req.email) < 5:
        raise HTTPException(400, "请输入有效邮箱")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少6位")
    if session.query(User).filter_by(email=req.email.lower()).first():
        raise HTTPException(400, "该邮箱已注册")

    user = User(
        id            = str(uuid.uuid4()),
        email         = req.email.lower(),
        password_hash = hash_password(req.password),
    )
    session.add(user)
    session.commit()
    return {"token": create_token(user.id), "email": user.email, "plan": user.plan,
            "pdf_count": 0, "query_count_today": 0}


@router.post("/login")
def login(req: AuthReq, session: Session = Depends(get_session)):
    user = session.query(User).filter_by(email=req.email.lower()).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")

    today = datetime.utcnow().date().isoformat()
    if user.query_date != today:
        user.query_count_today = 0
        user.query_date        = today
        session.commit()

    return {"token": create_token(user.id), "email": user.email, "plan": user.plan,
            "pdf_count": user.pdf_count, "query_count_today": user.query_count_today}


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
        "plan":              db_user.plan,
        "plan_expires_at":   str(db_user.plan_expires_at) if db_user.plan_expires_at else None,
        "pdf_count":         db_user.pdf_count,
        "query_count_today": db_user.query_count_today,
    }


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
