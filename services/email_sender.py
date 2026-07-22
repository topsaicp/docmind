"""
邮件发送服务（Brevo HTTP API）
环境变量：
  BREVO_API_KEY  Brevo 控制台生成的 API Key
  SENDER_EMAIL   已在 Brevo 验证的发件邮箱（默认 nufechx@126.com）
未配置时仅打印链接到日志，不影响本地开发。
"""
import os
import requests
from config import APP_URL

_BASE         = APP_URL.rstrip("/")
_BREVO_KEY    = os.getenv("BREVO_API_KEY", "")
_SENDER_EMAIL = os.getenv("SENDER_EMAIL", "nufechx@126.com")
_SUPPORT_MAIL = "topsaitech@163.com"


def send_verify_email(to_email: str, token: str) -> bool:
    verify_url = f"{_BASE}/api/auth/verify-email?token={token}"

    if not _BREVO_KEY:
        print(f"[email-dev] BREVO_API_KEY 未配置，验证链接：{verify_url}")
        return True

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept":       "application/json",
                "api-key":      _BREVO_KEY,
                "content-type": "application/json",
            },
            json={
                "sender":      {"name": "慧策智写", "email": _SENDER_EMAIL},
                "to":          [{"email": to_email}],
                "subject":     "验证您的慧策智写邮箱",
                "htmlContent": _verify_html(to_email, verify_url),
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[email] 发送成功 → {to_email}")
        return True
    except Exception as e:
        print(f"[email] 发送失败 ({type(e).__name__}): {e}")
        return False


def send_notify(to_email: str, subject: str, html: str) -> bool:
    """通用通知邮件（尊享版咨询线索、系统通知等）"""
    if not _BREVO_KEY:
        print(f"[email-dev] BREVO_API_KEY 未配置，通知未发送：{subject}")
        return True

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept":       "application/json",
                "api-key":      _BREVO_KEY,
                "content-type": "application/json",
            },
            json={
                "sender":      {"name": "慧策智写", "email": _SENDER_EMAIL},
                "to":          [{"email": to_email}],
                "subject":     subject,
                "htmlContent": html,
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[email] 通知发送成功 → {to_email}")
        return True
    except Exception as e:
        print(f"[email] 通知发送失败 ({type(e).__name__}): {e}")
        return False


def send_reset_email(to_email: str, token: str) -> bool:
    """发送密码重置邮件，链接有效期 1 小时。"""
    reset_url = f"{_BASE}/app?reset_token={token}"

    if not _BREVO_KEY:
        print(f"[email-dev] BREVO_API_KEY 未配置，重置链接：{reset_url}")
        return True

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept":       "application/json",
                "api-key":      _BREVO_KEY,
                "content-type": "application/json",
            },
            json={
                "sender":      {"name": "慧策智写", "email": _SENDER_EMAIL},
                "to":          [{"email": to_email}],
                "subject":     "慧策智写密码重置",
                "htmlContent": _reset_html(to_email, reset_url),
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[email] 重置邮件已发送 → {to_email}")
        return True
    except Exception as e:
        print(f"[email] 重置邮件发送失败 ({type(e).__name__}): {e}")
        return False


def _reset_html(email: str, reset_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f1a;font-family:'Helvetica Neue',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#1a1a28;border-radius:12px;border:1px solid rgba(255,255,255,.08);overflow:hidden">
        <tr><td style="background:linear-gradient(135deg,#1a1a28,#252535);padding:32px 40px;text-align:center;
                       border-bottom:1px solid rgba(201,168,76,.2)">
          <div style="font-size:22px;font-weight:700;color:#c9a84c;letter-spacing:1px">✦ 慧策智写</div>
          <div style="font-size:12px;color:#888;margin-top:4px">AI 学术文献助手</div>
        </td></tr>
        <tr><td style="padding:36px 40px">
          <h2 style="color:#e8e8f0;font-size:18px;font-weight:600;margin:0 0 12px">重置您的密码</h2>
          <p style="color:#aaa;font-size:14px;line-height:1.7;margin:0 0 28px">
            我们收到了您的密码重置申请。请点击下方按钮设置新密码，链接 <strong style="color:#aaa">1 小时</strong>内有效。
          </p>
          <div style="text-align:center;margin:0 0 28px">
            <a href="{reset_url}"
               style="display:inline-block;background:#c9a84c;color:#0f0f1a;font-weight:700;
                      font-size:15px;padding:13px 36px;border-radius:8px;text-decoration:none;
                      letter-spacing:.5px">
              ✓ 重置密码
            </a>
          </div>
          <p style="color:#666;font-size:12px;line-height:1.6;margin:0">
            如果此申请不是您本人发起，请忽略此邮件，您的账号仍然安全。<br>
            如按钮无法点击，请复制以下链接到浏览器：<br>
            <span style="color:#888;word-break:break-all">{reset_url}</span>
          </p>
        </td></tr>
        <tr><td style="padding:16px 40px;border-top:1px solid rgba(255,255,255,.06);
                       text-align:center;color:#555;font-size:11px">
          此邮件由慧策智写系统自动发送，请勿回复。如有问题请联系
          <a href="mailto:{_SUPPORT_MAIL}" style="color:#888">{_SUPPORT_MAIL}</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _verify_html(email: str, verify_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f1a;font-family:'Helvetica Neue',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#1a1a28;border-radius:12px;border:1px solid rgba(255,255,255,.08);overflow:hidden">
        <tr><td style="background:linear-gradient(135deg,#1a1a28,#252535);padding:32px 40px;text-align:center;
                       border-bottom:1px solid rgba(201,168,76,.2)">
          <div style="font-size:22px;font-weight:700;color:#c9a84c;letter-spacing:1px">✦ 慧策智写</div>
          <div style="font-size:12px;color:#888;margin-top:4px">AI 学术文献助手</div>
        </td></tr>
        <tr><td style="padding:36px 40px">
          <h2 style="color:#e8e8f0;font-size:18px;font-weight:600;margin:0 0 12px">验证您的邮箱地址</h2>
          <p style="color:#aaa;font-size:14px;line-height:1.7;margin:0 0 28px">
            您好，感谢注册慧策智写。请点击下方按钮完成邮箱验证，
            验证后即可开始使用 AI 文献问答、文献综述等全部功能。
          </p>
          <div style="text-align:center;margin:0 0 28px">
            <a href="{verify_url}"
               style="display:inline-block;background:#c9a84c;color:#0f0f1a;font-weight:700;
                      font-size:15px;padding:13px 36px;border-radius:8px;text-decoration:none;
                      letter-spacing:.5px">
              ✓ 验证邮箱
            </a>
          </div>
          <p style="color:#666;font-size:12px;line-height:1.6;margin:0">
            链接有效期 <strong style="color:#aaa">24 小时</strong>。
            如非本人操作，请忽略此邮件。<br>
            如按钮无法点击，请复制以下链接到浏览器：<br>
            <span style="color:#888;word-break:break-all">{verify_url}</span>
          </p>
        </td></tr>
        <tr><td style="padding:16px 40px;border-top:1px solid rgba(255,255,255,.06);
                       text-align:center;color:#555;font-size:11px">
          此邮件由慧策智写系统自动发送，请勿回复。如有问题请联系
          <a href="mailto:{_SUPPORT_MAIL}" style="color:#888">{_SUPPORT_MAIL}</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
