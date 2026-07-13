"""
支付相关数据模型
- pay_orders        支付订单
- activation_codes  激活码（人工成交/活动赠送/淘宝卡密）
- leads             专属尊享版咨询线索

与主项目一致：String 存 UUID、同步 SQLAlchemy。
注意：会员权益直接写入 users 表的 plan / plan_expires_at，不另建会员表。
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Numeric, DateTime, Text, Index

from db.database import Base   # 复用主项目的 Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PayOrder(Base):
    __tablename__ = "pay_orders"

    id         = Column(String, primary_key=True, default=_uuid)
    order_no   = Column(String, unique=True, nullable=False, index=True)  # 商户订单号
    user_id    = Column(String, nullable=False, index=True)
    plan       = Column(String, nullable=False)          # plus / pro
    days       = Column(Integer, nullable=False, default=30)
    amount     = Column(Numeric(10, 2), nullable=False)  # 元
    channel    = Column(String, nullable=False, default="alipay")  # alipay / code / manual
    status     = Column(String, nullable=False, default="pending", index=True)
    # pending 待支付 / paid 已支付 / closed 已关闭 / refunded 已退款
    trade_no   = Column(String, nullable=True)           # 支付宝交易号
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    paid_at    = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_pay_orders_user_status", "user_id", "status"),)


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    id         = Column(String, primary_key=True, default=_uuid)
    code       = Column(String, unique=True, nullable=False, index=True)
    plan       = Column(String, nullable=False)          # plus / pro / custom
    days       = Column(Integer, nullable=False)
    status     = Column(String, nullable=False, default="unused", index=True)
    # unused / used / void
    note       = Column(String, nullable=True)           # 备注：来源、批次
    used_by    = Column(String, nullable=True)
    used_at    = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Lead(Base):
    """专属尊享版咨询线索"""
    __tablename__ = "leads"

    id         = Column(String, primary_key=True, default=_uuid)
    user_id    = Column(String, nullable=True)           # 已登录用户则记录
    email      = Column(String, nullable=False)
    contact    = Column(String, nullable=True)           # 微信/QQ/手机
    need       = Column(Text, nullable=True)             # 需求描述
    status     = Column(String, nullable=False, default="new", index=True)
    # new 待跟进 / contacted 已联系 / won 已成交 / lost 已流失
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
