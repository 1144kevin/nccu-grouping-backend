"""Credential-free email confirmation landing page for private device tests."""

import base64
import hashlib
from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import Settings
from app.core.device_test import private_ipv4

PATH = "/api/v1/auth/email-confirmed"


def email_redirect(settings: Settings) -> str:
    value = settings.core_email_redirect_url
    if settings.core_device_test_enabled:
        value = f"https://{settings.core_device_test_host}:8443{PATH}"
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            settings.environment == "local"
            and parsed.scheme == "https"
            and private_ipv4(parsed.hostname or "")
            and parsed.port == 8443
            and parsed.path == PATH
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, "email_redirect_not_configured")
    return value


SCRIPT = """function showResult(){
const q=new URLSearchParams(location.search);
const h=new URLSearchParams(location.hash.slice(1));
const failed=q.has('error')||q.has('error_code')||h.has('error')||h.has('error_code');
const returned=h.has('access_token');
history.replaceState(null,'',location.pathname);
document.getElementById('result').textContent=failed
 ? '驗證連結已失效或無法使用，請重新取得驗證信。'
 : returned ? '驗證連結已處理。請回 Expo Go，用信箱與 App 密碼登入；登入結果以 Supabase 驗證為準。'
 : '這是信箱驗證回跳頁。請從最新的驗證信開啟連結，完成後回 Expo Go 登入。';
}
showResult();
window.addEventListener('hashchange',showResult);
"""


def confirmation_page() -> HTMLResponse:
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    return HTMLResponse(
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>政缺一咖 · 信箱驗證</title><h1>政缺一咖 · 信箱驗證</h1>"
        '<p id="result">正在整理驗證結果…</p>'
        "<p>此頁不會登入 Expo Go、不保存驗證憑證，也不會要求你輸入密碼。"
        "請自行切回 Expo Go 的登入頁。</p>"
        "<noscript>請回 Expo Go 嘗試登入；若仍提示未驗證，請使用最新驗證信。</noscript>"
        f"<script>{SCRIPT}</script></html>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'sha256-{digest}'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            ),
        },
    )
