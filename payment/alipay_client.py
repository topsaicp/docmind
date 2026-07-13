"""
支付宝客户端（电脑网站支付 + 手机网站支付）
依赖: pip install python-alipay-sdk pycryptodome

环境变量（写入 /www/docmind/env.sh）：
  ALIPAY_APPID                 开放平台应用 APPID
  ALIPAY_APP_PRIVATE_KEY_PATH  应用私钥 PEM 文件路径
  ALIPAY_PUBLIC_KEY_PATH       支付宝公钥 PEM 文件路径
  ALIPAY_NOTIFY_URL            https://app.topsaitech.com.cn/api/pay/alipay/notify
  ALIPAY_RETURN_URL            https://app.topsaitech.com.cn/app?pay=done
  ALIPAY_DEBUG                 "1"=沙箱，其他=正式
"""
import os
from functools import lru_cache

from alipay import AliPay

_GATEWAY_PROD = "https://openapi.alipay.com/gateway.do"
_GATEWAY_DEV  = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"


def _read_key(name: str) -> str:
    val = os.environ.get(name)
    if val:
        return val
    path = os.environ.get(name + "_PATH")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    raise RuntimeError(f"支付宝密钥未配置：{name} 或 {name}_PATH")


def is_debug() -> bool:
    return os.environ.get("ALIPAY_DEBUG") == "1"


def gateway() -> str:
    return _GATEWAY_DEV if is_debug() else _GATEWAY_PROD


@lru_cache()
def get_alipay() -> AliPay:
    return AliPay(
        appid=os.environ["ALIPAY_APPID"],
        app_notify_url=os.environ["ALIPAY_NOTIFY_URL"],
        app_private_key_string=_read_key("ALIPAY_APP_PRIVATE_KEY"),
        alipay_public_key_string=_read_key("ALIPAY_PUBLIC_KEY"),
        sign_type="RSA2",
        debug=is_debug(),
    )


def pay_url(order_no: str, amount: str, subject: str, mobile: bool = False) -> str:
    """生成支付宝收银台跳转链接。mobile=True 走手机网站支付。"""
    ap = get_alipay()
    common = dict(
        out_trade_no=order_no,
        total_amount=amount,
        subject=subject,
        return_url=os.environ.get("ALIPAY_RETURN_URL", ""),
        notify_url=os.environ["ALIPAY_NOTIFY_URL"],
    )
    if mobile:
        qs = ap.api_alipay_trade_wap_pay(**common)
    else:
        qs = ap.api_alipay_trade_page_pay(**common)
    return f"{gateway()}?{qs}"


def query(order_no: str) -> dict:
    return get_alipay().api_alipay_trade_query(out_trade_no=order_no)


def refund(order_no: str, amount: str, reason: str = "用户退款") -> dict:
    return get_alipay().api_alipay_trade_refund(
        out_trade_no=order_no, refund_amount=amount, refund_reason=reason)


def verify_notify(data: dict, signature: str) -> bool:
    return get_alipay().verify(data, signature)
