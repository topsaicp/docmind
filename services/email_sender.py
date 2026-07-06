"""
邮件发送服务（基于 Resend）
未配置 RESEND_API_KEY 时仅打印链接到日志，不影响本地开发。
"""
from config import RESEND_API_KEY, EMAIL_FROM, APP_URL


def send_verify_email(to_email: str, token: str) -> bool:
    verify_url = f"{APP_URL}/api/auth/verify-email?token={token}"

    if not RESEND_API_KEY:
        print(f"[email-dev] 验证链接（仅开发用，未配置 RESEND_API_KEY）：{verify_url}")
        return True

    try:
        import resend
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from":    EMAIL_FROM,
            "to":      [to_email],
            "subject": "验证您的 DocMind 邮箱",
            "html":    _verify_html(to_email, verify_url),
        })
        return True
    except Exception as e:
        print(f"[email] 发送失败: {e}")
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
