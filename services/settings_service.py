"""
站点配置服务：从 settings 表读取套餐与站点信息，5 分钟内存缓存。
数据库无记录时回退到 config.py 的硬编码默认值。
所有写操作调用 save_plans() / save_site() 并自动刷新缓存。
"""
import json
from datetime import datetime, timedelta
from config import PLAN_LIMITS

_CACHE: dict = {}
_CACHE_EXPIRY: dict = {}
_TTL = timedelta(minutes=5)

# ── 默认套餐配置（与 config.py PLAN_LIMITS 保持一致）──────────────────────
DEFAULT_PLANS: dict = {
    "free": {
        "name": "免费版",
        "amount": "0",
        "days": 0,
        "badge": "",
        "features": [
            "最多 5 篇 PDF 文献",
            "每日 20 次智能问答",
            "知识库问答 + 章节精读",
            "文献综述（最多 3 篇）",
            "引用格式导出（APA/MLA/GB·T）",
        ],
        "limits": {**PLAN_LIMITS["free"]},
    },
    "plus": {
        "name": "基础版",
        "amount": "9.90",
        "days": 30,
        "badge": "",
        "features": [
            "最多 30 篇 PDF 文献",
            "每日 100 次智能问答",
            "全部免费版功能",
            "PDF 截图智能分析（图表/公式/表格）",
            "文献综述（最多 10 篇）",
            "学术语言优化（3000 词/次）",
            "更长回复模式（2× token）",
            "专属邮件客服",
        ],
        "limits": {**PLAN_LIMITS["plus"]},
    },
    "pro": {
        "name": "专业版",
        "amount": "19.90",
        "days": 30,
        "badge": "最受欢迎",
        "features": [
            "最多 100 篇 PDF 文献",
            "无限每日问答次数",
            "全部基础版功能",
            "学术语言优化（10000 词/次）",
            "文献综述（最多 20 篇）",
            "超长回复模式（4× token）",
            "高峰期优先 API 配额",
            "优先客服支持（24h 响应）",
        ],
        "limits": {**PLAN_LIMITS["pro"]},
    },
    "custom": {
        "name": "专属尊享版",
        "amount": "定制",
        "days": 0,
        "badge": "企业首选",
        "features": [
            "全部专业版功能，额度不设上限",
            "1v1 学术顾问全程对接",
            "论文人工精修：语言与逻辑双维度",
            "投稿护航：期刊建议 + 格式适配",
            "返修支持：审稿意见解读与回复协助",
            "工作日 4 小时内优先响应",
        ],
        "limits": {**PLAN_LIMITS.get("enterprise", PLAN_LIMITS["pro"])},
    },
}

DEFAULT_SITE: dict = {
    "company_name": "南京道普斯慧策智能科技有限公司",
    "address": "",
    "support_email": "topsaitech@163.com",
    "wechat": "topsaitech",
    "qq": "3573552602",
    "phone": "",
    "icp": "苏ICP备2026046699号",
    "icp_url": "https://beian.miit.gov.cn",
    "announcement": "",
    "announcement_active": False,
    "announcement_type": "info",
}


# ── 内部缓存 ──────────────────────────────────────────────────────────────
def _load_from_db(key: str):
    try:
        from db.database import Session, Settings
        db = Session()
        try:
            row = db.query(Settings).filter_by(key=key).first()
            return json.loads(row.value) if row else None
        finally:
            db.close()
    except Exception:
        return None


def _save_to_db(key: str, value) -> None:
    from db.database import Session, Settings
    db = Session()
    try:
        row = db.query(Settings).filter_by(key=key).first()
        if row:
            row.value = json.dumps(value, ensure_ascii=False)
            row.updated_at = datetime.utcnow()
        else:
            db.add(Settings(key=key, value=json.dumps(value, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    invalidate_cache()


def invalidate_cache() -> None:
    _CACHE.clear()
    _CACHE_EXPIRY.clear()


def _get(key: str, default):
    now = datetime.utcnow()
    if key in _CACHE_EXPIRY and now < _CACHE_EXPIRY[key]:
        return _CACHE[key]
    val = _load_from_db(key)
    if val is None:
        val = default
    _CACHE[key] = val
    _CACHE_EXPIRY[key] = now + _TTL
    return val


# ── 公共接口 ──────────────────────────────────────────────────────────────
def get_plans() -> dict:
    return _get("plans", DEFAULT_PLANS)


def get_site() -> dict:
    return _get("site", DEFAULT_SITE)


def save_plans(data: dict) -> None:
    _save_to_db("plans", data)


def save_site(data: dict) -> None:
    _save_to_db("site", data)


def get_limits(plan: str) -> dict:
    """从动态配置取套餐限额，兼容现有调用方（config.get_limits 的动态替代）。"""
    plans = get_plans()
    entry = plans.get(plan) or plans.get("free", {})
    limits = entry.get("limits", {})
    fallback = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    return {**fallback, **limits}
