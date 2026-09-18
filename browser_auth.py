"""Browser-assisted authentication without Playwright or WebDriver.

The app launches an installed Chrome/Edge with a dedicated local profile and a
Chrome DevTools Protocol (CDP) debugging port. The user signs in normally; this
module reads only the cookies/request metadata needed by ArkSwift AutoListing.

No username/password is stored. No F12/Copy-as-cURL step is required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
import websocket

ROOT = Path(__file__).resolve().parent
AUTH_DIR = ROOT / "workspace" / "_auth"
PROFILE_DIR = ROOT / "workspace" / "_browser_profiles"
CL_AUTH = AUTH_DIR / "cl.json"
ARK_AUTH = ROOT / "sku_write" / "auth.json"  # Existing clients already read this path.

ARK_URL = "https://www.arkswift.com/seller-console/product/product-list"
CL_URL = "https://cl.aosom.cloud/Home/Index"


class BrowserAuthError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def has_arkswift_auth(market: str | None = None) -> bool:
    auth = _read_json(ARK_AUTH)
    if not str(auth.get("raw_cookie") or auth.get("authorization_web") or "").strip():
        return False
    if market is None:
        return True
    try:
        import project_config as pc
        info = pc.market_config(market)
    except Exception:
        return False
    return auth.get("market") == market and str(auth.get("store_id") or "") == info["store_id"]


def invalidate_arkswift_auth() -> None:
    """Remove the active ArkSwift credential when the selected store changes."""
    try:
        ARK_AUTH.unlink()
    except FileNotFoundError:
        pass


def has_cl_auth() -> bool:
    auth = _read_json(CL_AUTH)
    return bool(str(auth.get("raw_cookie") or "").strip() and str(auth.get("account") or "").strip())


def load_cl_auth() -> dict[str, Any]:
    auth = _read_json(CL_AUTH)
    if not has_cl_auth():
        raise FileNotFoundError("CL 登录会话尚未建立；请在控制台点击 CL → Sign in，或直接运行 RevFlow 让系统自动唤起登录。")
    return auth


def _candidate_browser_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("ARKSWIFT_BROWSER_EXE")
    if override:
        paths.append(Path(override))

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    pf = Path(os.environ.get("PROGRAMFILES", ""))
    pfx86 = Path(os.environ.get("PROGRAMFILES(X86)", ""))
    paths += [
        local / "Google/Chrome/Application/chrome.exe",
        pf / "Google/Chrome/Application/chrome.exe",
        pfx86 / "Google/Chrome/Application/chrome.exe",
        pf / "Microsoft/Edge/Application/msedge.exe",
        pfx86 / "Microsoft/Edge/Application/msedge.exe",
        local / "Microsoft/Edge/Application/msedge.exe",
    ]
    for name in ("chrome.exe", "msedge.exe", "chrome", "microsoft-edge", "msedge"):
        found = shutil.which(name)
        if found:
            paths.append(Path(found))
    # Preserve order and remove empty/duplicate paths.
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key and key not in seen:
            seen.add(key)
            out.append(path)
    return out


def find_browser() -> Path:
    for path in _candidate_browser_paths():
        if path.is_file():
            return path
    raise BrowserAuthError(
        "未找到 Google Chrome 或 Microsoft Edge。Windows 电脑通常自带 Edge；"
        "如使用便携浏览器，可设置环境变量 ARKSWIFT_BROWSER_EXE 指向 chrome.exe/msedge.exe。"
    )


def browser_summary() -> str:
    try:
        return str(find_browser())
    except BrowserAuthError as exc:
        return str(exc)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _debug_json(port: int, path: str, timeout: float = 1.0) -> Any:
    response = requests.get(f"http://127.0.0.1:{port}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def _wait_for_target(port: int, host: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            targets = _debug_json(port, "/json/list")
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            for target in pages:
                if urlsplit(str(target.get("url") or "")).hostname == host:
                    return target
            if pages:
                return pages[0]
        except Exception as exc:  # Browser may still be starting.
            last_error = exc
        time.sleep(0.25)
    raise BrowserAuthError(f"浏览器已启动，但无法连接调试会话：{last_error or '未发现页面'}")


def _cookie_header(cookies: list[dict[str, Any]], host: str) -> str:
    selected: list[str] = []
    for item in cookies:
        domain = str(item.get("domain") or "").lstrip(".").lower()
        if domain and (host == domain or host.endswith("." + domain)):
            name = str(item.get("name") or "").strip()
            if name:
                selected.append(f"{name}={item.get('value', '')}")
    return "; ".join(selected)


def _cookie_value(cookies: list[dict[str, Any]], host: str, name: str) -> str:
    wanted = name.lower()
    for item in cookies:
        domain = str(item.get("domain") or "").lstrip(".").lower()
        if (host == domain or host.endswith("." + domain)) and str(item.get("name") or "").lower() == wanted:
            return str(item.get("value") or "")
    return ""


@dataclass
class _Observed:
    account: str = ""
    user_agent: str = ""
    probe_url: str = ""
    probe_score: int = -1

    def request(self, params: dict[str, Any], host: str) -> None:
        request = params.get("request") or {}
        url = str(request.get("url") or "")
        if urlsplit(url).hostname != host:
            return
        headers = request.get("headers") or {}
        normalized = {str(k).lower(): str(v) for k, v in headers.items()}
        if normalized.get("account"):
            self.account = normalized["account"].strip()
        if normalized.get("user-agent"):
            self.user_agent = normalized["user-agent"].strip()

        path = urlsplit(url).path.lower()
        score = -1
        if "getauthorizebuttoncolumnlist" in path:
            score = 100
        elif "getusersearchjson" in path:
            score = 80
        elif "getlistforlanage" in path or "getlistforlanguage" in path:
            score = 60
        elif "visitmodule" in path:
            score = 50
        if request.get("method") == "GET" and score > self.probe_score:
            self.probe_url = url
            self.probe_score = score


class _CDP:
    def __init__(self, ws_url: str, observed: _Observed, host: str):
        self.ws = websocket.create_connection(ws_url, timeout=1.0, origin="http://127.0.0.1")
        self.next_id = 1
        self.observed = observed
        self.host = host

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def _handle(self, message: dict[str, Any]) -> None:
        if message.get("method") == "Network.requestWillBeSent":
            self.observed.request(message.get("params") or {}, self.host)

    def command(self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        ident = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            message = json.loads(raw)
            self._handle(message)
            if message.get("id") == ident:
                if message.get("error"):
                    raise BrowserAuthError(f"CDP {method} 失败：{message['error']}")
                return message.get("result") or {}
        raise BrowserAuthError(f"CDP {method} 超时。")

    def pump(self, seconds: float = 0.25) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                return
            self._handle(json.loads(raw))


def _launch(service: str, url: str) -> tuple[subprocess.Popen[Any], int, dict[str, Any], Path]:
    browser = find_browser()
    port = _free_port()
    profile = PROFILE_DIR / service
    profile.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [
            str(browser),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    target = _wait_for_target(port, urlsplit(url).hostname or "")
    return process, port, target, browser


def _capture(service: str, url: str, timeout: int = 300, market: str | None = None) -> dict[str, Any]:
    host = urlsplit(url).hostname or ""
    _, _, target, browser = _launch(service, url)
    observed = _Observed()
    cdp = _CDP(str(target["webSocketDebuggerUrl"]), observed, host)
    try:
        cdp.command("Network.enable")
        # Reload once after Network.enable so request headers (especially CL account)
        # are observable even when the dedicated profile was already signed in.
        try:
            cdp.command("Page.enable")
            cdp.command("Page.reload", {"ignoreCache": True})
        except BrowserAuthError:
            pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cdp.pump(0.35)
            cookies = cdp.command("Network.getAllCookies", timeout=3).get("cookies") or []
            raw_cookie = _cookie_header(cookies, host)
            if service.startswith("arkswift"):
                token = _cookie_value(cookies, host, "Authorization_web")
                if token:
                    if not market:
                        raise BrowserAuthError("ArkSwift 登录捕获缺少市场信息。")
                    import project_config as pc
                    info = pc.market_config(market)
                    result = {
                        "raw_cookie": raw_cookie,
                        "authorization_web": token,
                        "market": market,
                        "store_id": info["store_id"],
                        "captured_at": _now(),
                        "browser": str(browser),
                    }
                    _write_json(ARK_AUTH, result)
                    return result
            else:
                token = _cookie_value(cookies, host, "Aosom_ADMS_V7_Token")
                if token and observed.account:
                    result = {
                        "raw_cookie": raw_cookie,
                        "account": observed.account,
                        "user_agent": observed.user_agent,
                        "referer": CL_URL,
                        "probe_url": observed.probe_url,
                        "captured_at": _now(),
                        "browser": str(browser),
                    }
                    _write_json(CL_AUTH, result)
                    return result
            time.sleep(0.35)
    finally:
        cdp.close()
    raise BrowserAuthError(
        "等待网页登录超时。请在弹出的浏览器中完成登录后保持页面打开，再重试。"
    )


def capture_arkswift(market: str, timeout: int = 300) -> dict[str, Any]:
    # Each ArkSwift market/store gets its own browser profile. This prevents a
    # login for one store from overwriting another store's browser session.
    return _capture(f"arkswift_{market}", ARK_URL, timeout=timeout, market=market)


def capture_cl(timeout: int = 300) -> dict[str, Any]:
    return _capture("cl", CL_URL, timeout=timeout)


def validate_arkswift(market: str) -> bool:
    if not has_arkswift_auth(market):
        return False
    try:
        import project_config as pc
        from sku_write.arkswift_client import ArkSwiftClient
        client = ArkSwiftClient(pc.load_write_config()["arkswift"], pc.load_auth())
        response = client.session.get(
            client.base_url + "/rest/v1/seller/store/status",
            params={"_storeId": client.store_id, "_lang": client.lang},
            timeout=12,
            allow_redirects=False,
        )
        client._decode(response, "登录检测")
        return True
    except Exception:
        return False


def _cl_session(auth: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": str(auth.get("referer") or CL_URL),
        "User-Agent": str(auth.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36"),
        "Cookie": str(auth.get("raw_cookie") or ""),
        "account": str(auth.get("account") or ""),
    })
    return session


def validate_cl() -> bool:
    if not has_cl_auth():
        return False
    auth = load_cl_auth()
    session = _cl_session(auth)
    probe = str(auth.get("probe_url") or "").strip()
    if probe and urlsplit(probe).hostname != "cl.aosom.cloud":
        probe = ""
    try:
        if probe:
            response = session.get(probe, timeout=12, allow_redirects=True)
            final = response.url.lower()
            body = response.text[:1500].lower()
            if response.status_code in (401, 403) or "/login" in final:
                return False
            # A captured XHR returning JSON/normal application data is a strong auth probe.
            if response.status_code < 400 and (
                "json" in response.headers.get("content-type", "").lower()
                or '"code"' in body
                or "响应成功" in response.text[:1500]
            ):
                return True

        response = session.get(CL_URL, timeout=12, allow_redirects=True)
        final = response.url.lower()
        body = response.text[:12000].lower()
        if response.status_code in (401, 403) or "/login" in final:
            return False
        # The unauthenticated page shown by CL contains the sign-in form. The signed-in
        # shell contains the Country/Shop navigation and does not render that login card.
        login_markers = ("type=\"password\"", "sign in", "signin")
        app_markers = ("country list", "channel listing", "ca_pid_master")
        if any(marker in body for marker in app_markers):
            return True
        if any(marker in body for marker in login_markers):
            return False
        return response.status_code < 400
    except requests.RequestException:
        return False


def ensure_arkswift(market: str, timeout: int = 300) -> str:
    if validate_arkswift(market):
        return "existing"
    capture_arkswift(market, timeout=timeout)
    if not validate_arkswift(market):
        raise BrowserAuthError("ArkSwift 已捕获浏览器登录态，但 API 验证仍失败。请确认当前账号有该市场店铺权限。")
    return "captured"


def ensure_cl(timeout: int = 300) -> str:
    if validate_cl():
        return "existing"
    capture_cl(timeout=timeout)
    if not validate_cl():
        raise BrowserAuthError("CL 已捕获浏览器登录态，但连接验证仍失败。请保持登录页面完成状态后重试。")
    return "captured"
