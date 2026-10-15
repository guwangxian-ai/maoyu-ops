"""User-scoped Baidu OAuth and private credential persistence.

Endpoints and the public experience AppKey follow baidu-netdisk/mcp. Secrets
and account tokens never appear in API responses, browser storage, or logs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx


EXPERIENCE_CLIENT_ID = "QHOuRXiepJBMjtk0esLhrPoNlQyYd0mF"  # Public AppKey, not a secret.
AUTH_MANAGEMENT_URL = "https://passport.baidu.com/v6/appAuthority"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{6,4096}$")
_locks: dict[int, asyncio.Lock] = {}
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class BaiduDriveError(Exception):
    """A safe provider failure that can be displayed in the web UI."""


class BaiduTokenExpired(BaiduDriveError):
    pass


def redact(value: str) -> str:
    return re.sub(r"(?i)((?:access_token|refresh_token|client_secret|temp_bduss|bdstoken|bduss|stoken|code)[=\":\s]+)[^&\s\"'<>]+",
                  r"\1[redacted]", value)


class _CredentialLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "baidu.com" in message or record.name.startswith("mcp"):
            record.msg, record.args = redact(message), ()
            if record.exc_info:
                record.msg += "\n" + redact("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
                record.exc_text = None
        return True


# httpx logs request URLs at INFO; the provider's protocol puts tokens in query
# strings. Redact at the source logger before the project's Loguru forwarding.
for _logger_name in ("httpx", "mcp.client.sse", "mcp.client.session"):
    logging.getLogger(_logger_name).addFilter(_CredentialLogFilter())


def user_dir(user_id: int) -> Path:
    if user_id <= 0:
        raise BaiduDriveError("无效的用户账号")
    from app.core.config import get_settings
    path = Path(os.environ.get("BAIDU_AUTH_DIR", get_settings().baidu_auth_dir)).expanduser()
    root = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()
    for public in (PROJECT_ROOT / "backend-web/static", PROJECT_ROOT / "frontend/public", PROJECT_ROOT / "frontend/dist"):
        if root.is_relative_to(public):
            raise BaiduDriveError("百度授权目录必须位于静态资源目录之外")
    return root / f"user-{user_id}"


def read_private(path: Path, default: object = None):
    try:
        if not path.exists():
            return default
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("oversized private record")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaiduDriveError("读取百度网盘授权记录失败，请检查私有数据目录") from exc


def write_private(path: Path, data: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        raise BaiduDriveError("保存百度网盘授权记录失败，请检查私有数据目录") from exc
    finally:
        temporary.unlink(missing_ok=True)


@asynccontextmanager
async def exclusive(user_id: int):
    lock = _locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise BaiduDriveError("当前百度网盘请求正在执行，请完成后重试")
    async with lock:
        yield


def _config(user_id: int) -> dict:
    from app.core.config import get_settings
    settings = get_settings()
    default = {"mode": "application" if settings.baidu_client_id else "experience",
               "client_id": settings.baidu_client_id, "client_secret": settings.baidu_client_secret}
    data = read_private(user_dir(user_id) / "application.json", default)
    if not isinstance(data, dict):
        raise BaiduDriveError("百度网盘应用配置格式无效")
    return data


def get_config(user_id: int) -> dict:
    data = _config(user_id)
    return {"mode": data.get("mode", "experience"), "client_id": data.get("client_id", ""),
            "secret_configured": bool(data.get("client_secret")), "redirect_uri": "oob",
            "experience_client_id": EXPERIENCE_CLIENT_ID, "authorization_management_url": AUTH_MANAGEMENT_URL}


async def save_config(user_id: int, mode: str, client_id: str = "", client_secret: str = "") -> dict:
    async with exclusive(user_id):
        previous = _config(user_id)
        client_id, client_secret = client_id.strip(), client_secret.strip()
        secret = client_secret or (previous.get("client_secret", "") if previous.get("client_id") == client_id else "")
        if mode not in {"experience", "application"}:
            raise BaiduDriveError("授权方式无效")
        if mode == "application" and (not re.fullmatch(r"[A-Za-z0-9_-]{6,256}", client_id) or not TOKEN_PATTERN.fullmatch(secret)):
            raise BaiduDriveError("请填写百度开放平台应用的 AppKey 和 SecretKey")
        write_private(user_dir(user_id) / "application.json", {"mode": mode, "client_id": client_id, "client_secret": secret})
        (user_dir(user_id) / "pending.json").unlink(missing_ok=True)
        return get_config(user_id)


def _account(user_id: int) -> dict | None:
    data = read_private(user_dir(user_id) / "account.json")
    if data is None:
        return None
    if not isinstance(data, dict) or not data.get("access_token") or not data.get("account_id"):
        raise BaiduDriveError("百度网盘账号记录无效，请重新授权")
    return data


def public_status(account: dict | None, mode: str, *, verified: bool = False, message: str = "") -> dict:
    result = {"connected": bool(account), "available": True, "verified": verified, "auth_mode": mode}
    if account:
        for field in ("account_id", "nickname", "vip_type", "used_bytes", "capacity_bytes", "expires_at"):
            if account.get(field) is not None:
                result[field] = account[field]
    if message:
        result["message"] = message
    return result


def check_provider(data: object) -> dict:
    if not isinstance(data, dict):
        raise BaiduDriveError("百度网盘接口返回的数据格式不正确")
    code = data.get("errno", data.get("error_code", 0))
    if str(code) in {"110", "111", "-6"}:
        raise BaiduTokenExpired("百度网盘授权已失效，请重新登录授权")
    if str(code) not in {"0", "None"} or data.get("error"):
        # Use a stable message instead of blindly returning upstream token-bearing
        # error payloads. No retry for mutations with an uncertain outcome.
        if data.get("error") in {"invalid_grant", "expired_token"}:
            raise BaiduTokenExpired("百度授权码或刷新凭证已失效，请重新授权")
        if data.get("error") in {"invalid_client", "invalid_client_id", "invalid_client_secret"}:
            raise BaiduDriveError("百度应用配置无效，请核对 AppKey / SecretKey；体验应用可能已停用")
        raise BaiduDriveError(f"百度网盘请求失败（错误码 {str(code)[:24] if code else str(data.get('error'))[:40]}），请核对权限或稍后重试")
    return data


async def provider_json(url: str, *, params: dict | None = None, data: dict | None = None,
                        files: dict | None = None, timeout: float = 30) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request("POST" if data is not None or files is not None else "GET",
                                            url, params=params, data=data, files=files)
            response.raise_for_status()
            return check_provider(response.json())
    except BaiduDriveError:
        raise
    except httpx.TimeoutException as exc:
        raise BaiduDriveError("百度网盘请求超时；已提交的修改可能仍在处理，请刷新核验后再操作") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise BaiduDriveError("无法连接百度网盘接口，请检查网络后重试") from exc


async def _profile(access_token: str) -> tuple[dict, str]:
    user = await provider_json("https://pan.baidu.com/rest/2.0/xpan/nas", params={"method": "uinfo", "access_token": access_token})
    uid = str(user.get("uk") or "")
    if not uid.isdigit() or uid == "0":
        raise BaiduDriveError("百度未返回有效账号信息，无法保存此授权")
    try:
        profile = {"account_id": hashlib.sha256(uid.encode()).hexdigest()[:32],
                   "nickname": str(user.get("netdisk_name") or user.get("baidu_name") or "百度网盘用户")[:100],
                   "vip_type": int(user.get("vip_type") or 0)}
    except (ValueError, TypeError) as exc:
        raise BaiduDriveError("百度返回的账号属性格式无效") from exc
    notice = ""
    try:
        quota = await provider_json("https://pan.baidu.com/api/quota", params={"access_token": access_token, "checkfree": 1, "checkexpire": 1})
        profile.update({"used_bytes": max(0, int(quota.get("used") or 0)), "capacity_bytes": max(0, int(quota.get("total") or 0))})
    except BaiduTokenExpired:
        raise
    except (BaiduDriveError, ValueError, TypeError):
        notice = "账号已连接，容量暂不可用，请稍后检查连接"
    return profile, notice


def _provider_expiration(tokens: dict) -> int | None:
    duration = tokens.get("expires_in")
    if duration is None:
        return None
    try:
        duration = int(duration)
        if not 0 < duration <= 366 * 86400:
            raise ValueError("expiration")
        return int((time.time() + duration) * 1000)
    except (ValueError, TypeError, OverflowError) as exc:
        raise BaiduDriveError("百度返回的授权有效期无效，请重新授权") from exc


async def start_authorization(user_id: int) -> dict:
    async with exclusive(user_id):
        config = _config(user_id)
        mode = config.get("mode", "experience")
        state, expires_at = uuid4().hex, int((time.time() + 600) * 1000)
        if mode == "application" and (not config.get("client_id") or not config.get("client_secret")):
            raise BaiduDriveError("请先配置百度开放平台应用")
        write_private(user_dir(user_id) / "pending.json", {"state": state, "expires_at": expires_at, "mode": mode})
        credential_type = "authorization_code" if mode == "application" else "access_token"
        query = {"response_type": "code" if mode == "application" else "token",
                 "client_id": config["client_id"] if mode == "application" else EXPERIENCE_CLIENT_ID,
                 "redirect_uri": "oob", "scope": "basic,netdisk", "state": state, "display": "page"}
        message = "请在百度官方页面登录并授权，将返回的授权码粘贴到此处" if mode == "application" else "个人体验应用由百度提供，可能停止或更换；仅用于本地测试，正式运营请配置自己的开放平台应用"
        return {"authorization_url": "https://openapi.baidu.com/oauth/2.0/authorize?" + urlencode(query),
                "mode": mode, "credential_type": credential_type, "message": message, "expires_at": expires_at}


def parse_credential(user_id: int, credential: str, credential_type: str) -> dict:
    value = credential.strip()
    fields = {}
    if value.startswith(("https://", "http://")):
        try:
            url = urlsplit(value)
            valid_origin = url.scheme == "https" and url.hostname in {"openapi.baidu.com", "pan.baidu.com", "passport.baidu.com"} and url.port in {None, 443} and not url.username and not url.password
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise BaiduDriveError("请粘贴百度官方授权后的地址，或只粘贴 Access Token / 授权码")
        fields = {**parse_qs(url.query), **parse_qs(url.fragment)}
        if fields.get("error"):
            raise BaiduDriveError("百度未完成授权，请检查应用配置后重新登录")
        pending = read_private(user_dir(user_id) / "pending.json", {})
        if fields.get("state"):
            if not pending or pending.get("expires_at", 0) < time.time() * 1000 or fields["state"][0] != pending.get("state"):
                raise BaiduDriveError("授权请求不匹配或已过期，请重新打开百度授权页面")
        field = "code" if credential_type == "authorization_code" else "access_token"
        value = fields.get(field, [""])[0]
    if not TOKEN_PATTERN.fullmatch(value):
        raise BaiduDriveError("授权凭证格式无效，请复制完整的 Access Token 或授权码")
    result = {"value": value}
    if fields.get("expires_in"):
        try:
            duration = int(fields["expires_in"][0])
            if not 0 < duration <= 366 * 86400:
                raise ValueError("expiration")
            result["expires_at"] = int((time.time() + duration) * 1000)
        except ValueError as exc:
            raise BaiduDriveError("授权有效期无效，请重新复制授权结果") from exc
    if fields.get("refresh_token") and TOKEN_PATTERN.fullmatch(fields["refresh_token"][0]):
        result["refresh_token"] = fields["refresh_token"][0]
    return result


async def complete_authorization(user_id: int, credential: str, credential_type: str) -> dict:
    async with exclusive(user_id):
        parsed, config = parse_credential(user_id, credential, credential_type), _config(user_id)
        tokens = {"access_token": parsed["value"], "expires_at": parsed.get("expires_at"), "refresh_token": parsed.get("refresh_token")}
        if credential_type == "authorization_code":
            if config.get("mode") != "application" or not config.get("client_secret"):
                raise BaiduDriveError("授权码模式需要配置自己的 AppKey 和 SecretKey")
            tokens = await provider_json("https://openapi.baidu.com/oauth/2.0/token", data={"grant_type": "authorization_code",
                "code": parsed["value"], "client_id": config["client_id"], "client_secret": config["client_secret"], "redirect_uri": "oob"})
            tokens["expires_at"] = _provider_expiration(tokens)
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not TOKEN_PATTERN.fullmatch(access_token):
            raise BaiduDriveError("百度没有返回有效 Access Token")
        profile, notice = await _profile(access_token)
        account = {**profile, "access_token": access_token, "refresh_token": tokens.get("refresh_token"),
                   "expires_at": tokens.get("expires_at"), "auth_mode": config.get("mode", "experience"),
                   "refresh_client_id": config.get("client_id"), "refresh_client_secret": config.get("client_secret"),
                   "authorized_at": int(time.time() * 1000)}
        write_private(user_dir(user_id) / "account.json", account)
        (user_dir(user_id) / "pending.json").unlink(missing_ok=True)
        return public_status(account, account["auth_mode"], verified=True, message=notice)


async def account_token(user_id: int, *, force_refresh: bool = False) -> tuple[dict, str]:
    account = _account(user_id)
    if not account:
        raise BaiduDriveError("请先登录授权百度网盘")
    expires_at = account.get("expires_at") or 0
    if force_refresh or (expires_at and expires_at < (time.time() + 60) * 1000):
        if not all(account.get(field) for field in ("refresh_token", "refresh_client_id", "refresh_client_secret")):
            raise BaiduTokenExpired("百度网盘授权已过期，此体验凭证无法自动续期，请重新授权")
        tokens = await provider_json("https://openapi.baidu.com/oauth/2.0/token", data={"grant_type": "refresh_token",
            "refresh_token": account["refresh_token"], "client_id": account["refresh_client_id"], "client_secret": account["refresh_client_secret"]})
        if not TOKEN_PATTERN.fullmatch(str(tokens.get("access_token") or "")):
            raise BaiduDriveError("百度未返回有效的续期凭证")
        account.update({"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token") or account["refresh_token"],
                        "expires_at": _provider_expiration(tokens)})
        write_private(user_dir(user_id) / "account.json", account)
    return account, account["access_token"]


async def get_status(user_id: int) -> dict:
    mode = _config(user_id).get("mode", "experience")
    account = _account(user_id)
    if not account:
        return public_status(None, mode)
    mode = account.get("auth_mode", mode)
    if _locks.setdefault(user_id, asyncio.Lock()).locked():
        return public_status(account, mode, message="当前网盘任务正在执行，完成后可检查连接")
    async with exclusive(user_id):
        try:
            account, token = await account_token(user_id)
            try:
                profile, notice = await _profile(token)
            except BaiduTokenExpired:
                account, token = await account_token(user_id, force_refresh=True)
                profile, notice = await _profile(token)
            if profile["account_id"] != account["account_id"]:
                raise BaiduTokenExpired("百度返回的账号与已保存账号不一致，请重新授权")
            account.update(profile)
            write_private(user_dir(user_id) / "account.json", account)
            return public_status(account, mode, verified=True, message=notice)
        except BaiduTokenExpired as exc:
            return public_status(None, mode, message=str(exc))
        except BaiduDriveError as exc:
            # A transient provider outage must never discard authorization.
            return public_status(account, mode, message=str(exc))


async def logout(user_id: int) -> dict:
    async with exclusive(user_id):
        for name in ("account.json", "pending.json"):
            (user_dir(user_id) / name).unlink(missing_ok=True)
        return {"connected": False, "message": "已退出本项目中的百度网盘账号；彻底解除应用权限请前往百度授权管理",
                "authorization_management_url": AUTH_MANAGEMENT_URL}
