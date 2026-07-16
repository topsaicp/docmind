"""
公开站点配置接口（无需登录）
GET /api/plans        → 套餐信息（供前台定价渲染）
GET /api/site/config  → 站点公开信息（联系方式、公告等）
"""
from fastapi import APIRouter
from services import settings_service

router = APIRouter(tags=["site"])


@router.get("/api/plans")
def get_plans():
    return settings_service.get_plans()


@router.get("/api/site/config")
def get_site_config():
    cfg = settings_service.get_site()
    # 只返回前台安全展示的字段
    return {
        "support_email":       cfg.get("support_email", ""),
        "wechat":              cfg.get("wechat", ""),
        "qq":                  cfg.get("qq", ""),
        "phone":               cfg.get("phone", ""),
        "company_name":        cfg.get("company_name", ""),
        "icp":                 cfg.get("icp", ""),
        "icp_url":             cfg.get("icp_url", ""),
        "announcement":        cfg.get("announcement", ""),
        "announcement_active": cfg.get("announcement_active", False),
        "announcement_type":   cfg.get("announcement_type", "info"),
    }
