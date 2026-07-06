"""
邮件发送服务（SMTP，优先使用 163 等 SMTP 服务）
环境变量：
  SMTP_USER  发件邮箱，如 njuechx@163.com
  SMTP_PASS  163 授权码（不是登录密码）
未配置时仅打印链接到日志，不影响本地开发。
"""
import os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from config import APP_URL

_BASE      = APP_URL.rstrip("/")
_SMTP_USER = os.getenv("SMTP_USER", "")
_SMTP_PASS = os.getenv("SMTP_PASS", "")

# 根据发件邮箱后缀自动选 SMTP 服务器
_SMTP_SERVERS = {
    "163.com":  ("smtp.163.com",  465),
    "126.com":  ("smtp.126.com",  465),
    "qq.com":   ("smtp.qq.com",   465),
    "gmail.com":("smtp.gmail.com", 587),
    "yeah.net": ("smtp.yeah.net", 465),
}
_DEFAULT_SMTP = ("smtp.163.com", 465)


def _get_smtp_server() -> tuple[str, int]:
    if not _SMTP_USER:
        return _DEFAULT_SMTP
    domain = _SMTP_USER.split("@")[-1].lower()
    return _SMTP_SERVERS.get(domain, _DEFAULT_SMTP)


def send_verify_email(to_email: str, token: str) -> bool:
    verify_url = f"{_BASE}/api/auth/verify-email?token={token}"

    if not _SMTP_USER or not _SMTP_PASS:
        print(f"[email-dev] SMTP 未配置，验证链接：{verify_url}")
        return True

    host, port = _get_smtp_server()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "验证您的 DocMind 邮箱"
    msg["From"]    = f"DocMind <{_SMTP_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(_verify_html(to_email, verify_url), "html", "utf-8"))

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as s:
                s.login(_SMTP_USER, _SMTP_PASS)
                s.sendmail(_SMTP_USER, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls()
                s.login(_SMTP_USER, _SMTP_PASS)
                s.sendmail(_SMTP_USER, [to_email], msg.as_string())
        print(f"[email] 发送成功 → {to_email}")
        return True
    except Exception as e:
        print(f"[email] 发送失败 ({type(e).__name__}): {e}")
        return False


def _verify_html(email: str, verify_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f1a;font-family:'Helvetica Neue',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#1a1a28;border-radius:12px;border:1px solid rgba(255,255,255,.08);overflow:hidden">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#1a1a28,#252535);padding:32px 40px;text-align:center;
                       border-bottom:1px solid rgba(201,168,76,.2)">
          <div style="font-size:22px;font-weight:700;color:#c9a84c;letter-spacing:1px">✦ DocMind</div>
          <div style="font-size:12px;color:#888;margin-top:4px">AI 学术文献助手</div>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:36px 40px">
          <h2 style="color:#e8e8f0;font-size:18px;font-weight:600;margin:0 0 12px">验证您的邮箱地址</h2>
          <p style="color:#aaa;font-size:14px;line-height:1.7;margin:0 0 28px">
            您好，感谢注册 DocMind。请点击下方按钮完成邮箱验证，
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
        <!-- Footer -->
        <tr><td style="padding:16px 40px;border-top:1px solid rgba(255,255,255,.06);
                       text-align:center;color:#555;font-size:11px">
          此邮件由 DocMind 系统自动发送，请勿回复。如有问题请联系
          <a href="mailto:topsai@protonmail.com" style="color:#888">topsai@protonmail.com</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
