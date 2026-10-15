"""Owner-bound Baidu QR sessions; credentials never enter browser responses.

Protocol endpoints are documented in aki66938/BaiduPCS-Go-HTTP at commit
3d019e2aa71ea7208ad0866bcd60cd9904a24cba, internal/pcscommand/qrlogin.go.
This adapter uses independent httpx cookie jars, validated HTTPS redirects and
real BaiduPCS-Go account/root probes before committing a private connection.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

from app.services import baidu_pcs_cli as cli
from app.services.baidu_drive_auth import (
    BaiduDriveError, BaiduTokenExpired, exclusive, read_private, user_dir, write_private,
)

MODE = "baidupcs-go"
AUTH_MANAGEMENT_URL = "https://passport.baidu.com/v6/appAuthority"
QR_URL = "https://passport.baidu.com/v2/api/getqrcode"
POLL_URL = "https://passport.baidu.com/channel/unicast"
EXCHANGE_URL = "https://passport.baidu.com/v3/login/main/qrbdusslogin"
PAN_URL = "https://pan.baidu.com/disk/main"
PENDING = {"starting", "waiting", "scanned", "authorizing"}
MAX_SESSIONS = 64
SESSION_SECONDS = 300
_sessions: dict[str, "Session"] = {}
_registry_lock = asyncio.Lock()
_verified: dict[int, tuple[str, float, dict]] = {}


def get_config(user_id: int) -> dict:
    user_dir(user_id)
    return {"mode": MODE, "source": "qjfoidnh/BaiduPCS-Go", "root_path": cli.ROOT,
            "supports_qrcode": True, "authorization_management_url": AUTH_MANAGEMENT_URL}


def account(user_id: int) -> dict | None:
    record = read_private(user_dir(user_id) / "pcs/active.json")
    if record is None:
        return None
    if not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("account_id", ""))):
        raise BaiduDriveError("百度账号连接记录无效，请重新扫码登录")
    # Strict allowlist even if a private file was written by an older version.
    return {key: record[key] for key in ("account_id", "nickname", "uid", "used_bytes", "capacity_bytes") if key in record}


async def require_account(user_id: int) -> dict:
    record = account(user_id)
    if not record or not cli.config_path(user_id).is_file():
        raise BaiduTokenExpired("请先扫码登录百度网盘")
    return record


def _profile(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise BaiduDriveError("百度未返回有效账号信息，无法验证登录")
    uid = str(raw.get("uid", ""))
    if not re.fullmatch(r"[0-9]{1,24}", uid) or int(uid) <= 0:
        raise BaiduTokenExpired("百度未返回有效账号标识，请重新扫码登录")
    record = {"account_id": hashlib.sha256(("baidupcs:" + uid).encode()).hexdigest()[:32], "uid": uid}
    name = raw.get("name") or raw.get("nickname")
    if isinstance(name, str) and name.strip():
        record["nickname"] = name[:100]
    quota = raw.get("quota") if isinstance(raw.get("quota"), dict) else raw
    for field, source in (("used_bytes", "used"), ("capacity_bytes", "total")):
        value = quota.get(field, quota.get(source))
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            record[field] = value
    return record


async def _probe(user_id: int, credentials: dict | None = None) -> dict:
    profile = _profile(await cli.run(user_id, "profile", credentials=credentials))
    files = await cli.run(user_id, "list", {"dir": "/", "page": 1, "page_size": 1}, credentials=credentials)
    if not isinstance(files, dict) or not isinstance(files.get("files"), list):
        raise BaiduDriveError("百度根目录未通过真实读取验证")
    return profile


async def get_status(user_id: int) -> dict:
    result = {"connected": False, "available": True, "verified": False, "auth_mode": MODE, "root_path": cli.ROOT}
    try:
        cli.binary()
    except BaiduDriveError as exc:
        return {**result, "available": False, "message": str(exc)}
    async with cli.read_lock(user_id):
        record = account(user_id)
        if not record or not cli.config_path(user_id).is_file():
            return {**result, "message": "请使用百度网盘手机 App 扫码登录"}
        result.update({key: value for key, value in record.items() if key != "uid"}, connected=True)
        cached = _verified.get(user_id)
        if cached and cached[0] == record["account_id"] and time.monotonic() - cached[1] < 30:
            return {**result, **{key: value for key, value in cached[2].items() if key != "uid"}, "verified": True,
                    "message": "登录及网盘根目录读取已验证"}
        try:
            profile = await _probe(user_id)
            if profile["account_id"] != record["account_id"]:
                raise BaiduTokenExpired("当前凭证与已绑定账号不一致，请重新扫码登录")
            _verified[user_id] = (record["account_id"], time.monotonic(), profile)
            result.update({key: value for key, value in profile.items() if key != "uid"}, verified=True,
                          message="登录及网盘根目录读取已验证")
        except BaiduTokenExpired as exc:
            _verified.pop(user_id, None)
            result.update(connected=False, message=str(exc))
        except BaiduDriveError as exc:
            _verified.pop(user_id, None)
            result["message"] = str(exc)
        return result


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=True, follow_redirects=False, timeout=httpx.Timeout(35, connect=15),
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"})


def _auth_url(value: str) -> str:
    cli.trusted_url(value, authorization=True)
    if urlsplit(value).hostname not in {"passport.baidu.com", "pan.baidu.com"}:
        raise BaiduDriveError("百度登录返回了未允许的跳转地址")
    return value


async def _request(client: httpx.AsyncClient, url: str, *, params: dict | None = None,
                   limit: int = 256 * 1024) -> tuple[bytes, str]:
    for _ in range(6):
        _auth_url(url)
        async with client.stream("GET", url, params=params, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise BaiduDriveError("百度登录跳转缺少地址")
                url, params = _auth_url(urljoin(str(response.url), location)), None
                continue
            response.raise_for_status()
            result = bytearray()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > limit:
                    raise BaiduDriveError("百度授权响应过大，请重新扫码登录")
            return bytes(result), response.headers.get("content-type", "")
    raise BaiduDriveError("百度登录跳转次数异常，请重新扫码登录")


def _json(body: bytes) -> dict:
    try:
        text = body.decode("utf-8").strip()
        if not text.startswith("{"):
            match = re.fullmatch(r"(?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)?\s*\((.*)\)\s*;?", text, re.S)
            if not match:
                raise ValueError("invalid JSONP")
            text = match[1]
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("invalid JSON object")
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BaiduDriveError("百度授权响应格式无效，请稍后重新扫码") from exc


def _pan_credentials(client: httpx.AsyncClient) -> dict:
    # Prefer pan.baidu.com's STOKEN over passport's unrelated service token.
    selected: dict[str, tuple[int, str]] = {}
    for cookie in client.cookies.jar:
        domain = cookie.domain.lstrip(".").lower()
        if domain not in {"baidu.com", "pan.baidu.com"} or cookie.is_expired():
            continue
        if cookie.path and cookie.path != "/" and not "/api/list".startswith(cookie.path.rstrip("/") + "/"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cookie.name) or not cookie.value or any(
            ord(c) < 33 or ord(c) > 126 or c in ";\"\\" for c in cookie.value
        ):
            continue
        priority = len(domain) + len(cookie.path or "/")
        if cookie.name not in selected or priority > selected[cookie.name][0]:
            selected[cookie.name] = (priority, cookie.value)
    values = {key: value[1] for key, value in selected.items()}
    credential = {"bduss": values.get("BDUSS", ""), "stoken": values.get("STOKEN", ""),
                  "cookies": "; ".join(f"{key}={value}" for key, value in values.items()), "uid": ""}
    return cli._credentials(credential)


@dataclass
class Session:
    owner: int
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "starting"
    expires_at: float = field(default_factory=lambda: time.time() + SESSION_SECONDS)
    message: str = "正在获取百度登录二维码"
    qrcode_image: str = ""
    sign: str = ""
    client: httpx.AsyncClient | None = None
    task: asyncio.Task | None = None
    ready: asyncio.Future | None = None

    def view(self) -> dict:
        return {"session_id": self.id, "status": self.status, "expires_at": int(self.expires_at * 1000),
                "qrcode_image": self.qrcode_image if self.status in {"waiting", "scanned"} else "",
                "mode": MODE, "message": self.message}


def _get(user_id: int, session_id: str) -> Session:
    session = _sessions.get(session_id)
    if not session or session.owner != user_id:
        raise BaiduDriveError("扫码会话已结束或无权访问，请重新开始")
    return session


async def _exchange(session: Session, temporary: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{6,4096}", temporary):
        raise BaiduDriveError("百度扫码确认凭证格式无效")
    assert session.client is not None
    session.status, session.message = "authorizing", "手机已确认，正在验证百度网盘账号"
    await _request(session.client, EXCHANGE_URL, params={"bduss": temporary, "u": PAN_URL}, limit=2 * 1024**2)
    # Passport cookies alone are insufficient: establish the netdisk service
    # session, then select only cookies applicable to pan.baidu.com.
    await _request(session.client, PAN_URL, limit=2 * 1024**2)
    credential = _pan_credentials(session.client)
    profile = await _probe(session.owner, credential)
    credential["uid"] = profile["uid"]
    async with exclusive(session.owner):
        if session.status != "authorizing" or session.expires_at <= time.time():
            raise BaiduDriveError("扫码会话已结束，请重新开始")
        previous = account(session.owner)
        path = user_dir(session.owner) / "pcs/connections" / profile["account_id"] / "credentials.json"
        cli.private_directory(path.parent)
        write_private(path, credential)
        try:
            write_private(user_dir(session.owner) / "pcs/active.json", profile)
        except BaiduDriveError:
            if not previous or previous["account_id"] != profile["account_id"]:
                path.unlink(missing_ok=True)
            raise
        _verified[session.owner] = (profile["account_id"], time.monotonic(), profile)
        session.status, session.message = "authorized", "百度网盘登录成功，已验证原有文件读取权限"
        if previous and previous["account_id"] != profile["account_id"]:
            old = user_dir(session.owner) / "pcs/connections" / previous["account_id"] / "credentials.json"
            try:
                old.unlink(missing_ok=True)
            except OSError:
                session.message = "新账号登录已保存；旧凭证清理失败，请检查项目私有目录权限"


async def _run(session: Session) -> None:
    try:
        async with asyncio.timeout(SESSION_SECONDS):
            session.client = _client()
            body, _ = await _request(session.client, QR_URL, params={"lp": "pc"})
            data = _json(body)
            if str(data.get("errno")) != "0" or not isinstance(data.get("sign"), str) or not re.fullmatch(r"[A-Za-z0-9._~-]{6,1024}", data["sign"]):
                raise BaiduDriveError("百度未返回有效登录二维码")
            image_url = data.get("imgurl")
            if not isinstance(image_url, str):
                raise BaiduDriveError("百度未返回有效二维码图片")
            if not image_url.startswith("https://"):
                image_url = "https:" + image_url if image_url.startswith("//") else "https://" + image_url
            image, content_type = await _request(session.client, image_url, limit=2 * 1024**2)
            if not image.startswith(b"\x89PNG\r\n\x1a\n") or not content_type.lower().startswith("image/png"):
                raise BaiduDriveError("百度未返回有效 PNG 二维码")
            session.sign = data["sign"]
            session.qrcode_image = "data:image/png;base64," + base64.b64encode(image).decode()
            session.status, session.message = "waiting", "请使用百度网盘手机 App 扫码，并在手机上确认登录"
            if session.ready and not session.ready.done():
                session.ready.set_result(None)
            while session.status in {"waiting", "scanned"}:
                if session.expires_at <= time.time():
                    raise TimeoutError
                try:
                    body, _ = await _request(session.client, POLL_URL, params={"channel_id": session.sign, "callback": ""})
                except httpx.ReadTimeout:
                    # The real endpoint long-polls while no phone has scanned.
                    continue
                response = _json(body)
                errno = str(response.get("errno"))
                if errno not in {"0", "1"}:
                    raise BaiduDriveError("百度扫码状态检查失败，请重新生成二维码")
                channel = response.get("channel_v")
                if errno == "1" or not channel:
                    await asyncio.sleep(2)
                    continue
                if isinstance(channel, str):
                    channel = _json(channel.encode())
                if not isinstance(channel, dict):
                    raise BaiduDriveError("百度扫码状态格式无效")
                if str(channel.get("status")) == "0" and isinstance(channel.get("v"), str) and channel["v"]:
                    await _exchange(session, channel["v"])
                    break
                if str(channel.get("status")) == "1":
                    session.status, session.message = "scanned", "已扫码，请在手机上确认登录"
                else:
                    raise BaiduDriveError("百度未确认本次扫码登录，请重新生成二维码")
                await asyncio.sleep(2)
    except TimeoutError:
        session.status, session.message = "expired", "二维码已过期，请重新生成"
    except asyncio.CancelledError:
        if session.status in PENDING:
            session.status, session.message = "cancelled", "已取消本次扫码登录"
        raise
    except (BaiduDriveError, httpx.HTTPError, OSError, ValueError):
        session.status, session.message = "failed", "百度扫码登录未完成，请检查网络并重新扫码；原有账号未切换"
    finally:
        session.sign, session.qrcode_image = "", ""
        if session.client:
            session.client.cookies.clear()
            await session.client.aclose()
            session.client = None
        if session.ready and not session.ready.done():
            session.ready.set_result(None)


async def _cancel(session: Session, *, expired: bool = False) -> None:
    if session.status in PENDING:
        session.status = "expired" if expired else "cancelled"
        session.message = "二维码已过期，请重新生成" if expired else "已取消本次扫码登录"
        if session.task:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
    session.sign, session.qrcode_image = "", ""


async def start_authorization(user_id: int, confirmed: bool) -> dict:
    if confirmed is not True:
        raise BaiduDriveError("请先确认在当前项目保存百度网盘登录授权")
    cli.binary()
    async with exclusive(user_id):
        async with _registry_lock:
            for old in list(_sessions.values()):
                if old.owner == user_id or old.expires_at <= time.time():
                    await _cancel(old, expired=old.expires_at <= time.time())
                    _sessions.pop(old.id, None)
            if len(_sessions) >= MAX_SESSIONS:
                for old in list(_sessions.values()):
                    if old.status not in PENDING:
                        _sessions.pop(old.id, None)
            if len(_sessions) >= MAX_SESSIONS:
                raise BaiduDriveError("扫码登录请求过多，请稍后重试")
            session = Session(user_id)
            session.ready = asyncio.get_running_loop().create_future()
            _sessions[session.id] = session
            session.task = asyncio.create_task(_run(session))
    try:
        await asyncio.wait_for(asyncio.shield(session.ready), 50)
    except (TimeoutError, asyncio.CancelledError):
        await _cancel(session)
        if asyncio.current_task().cancelling():
            raise
        raise BaiduDriveError("获取百度二维码超时，请检查网络后重试") from None
    if session.status not in {"waiting", "scanned", "authorizing", "authorized"}:
        raise BaiduDriveError(session.message)
    return session.view()


async def session_status(user_id: int, session_id: str) -> dict:
    session = _get(user_id, session_id)
    if session.expires_at <= time.time() and session.status in PENDING:
        await _cancel(session, expired=True)
    return session.view()


async def cancel(user_id: int, session_id: str) -> dict:
    if session_id not in _sessions and re.fullmatch(r"[0-9a-f]{32}", session_id):
        return {"session_id": session_id, "status": "cancelled", "expires_at": 0,
                "qrcode_image": "", "mode": MODE, "message": "扫码会话已结束"}
    session = _get(user_id, session_id)
    await _cancel(session)
    return session.view()


async def shutdown() -> None:
    for session in list(_sessions.values()):
        await _cancel(session)


async def logout(user_id: int) -> dict:
    async with exclusive(user_id):
        for session in list(_sessions.values()):
            if session.owner == user_id:
                await _cancel(session)
        root = user_dir(user_id) / "pcs"
        (root / "active.json").unlink(missing_ok=True)
        if (root / "connections").exists():
            shutil.rmtree(root / "connections")
        (root / "credentials.json").unlink(missing_ok=True)
        _verified.pop(user_id, None)
    return {"connected": False, "auth_mode": MODE, "root_path": cli.ROOT,
            "message": "已退出当前项目的百度账号并清除本地凭证；百度手机客户端登录不受影响"}
