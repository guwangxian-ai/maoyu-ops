"""Web sessions around the unchanged official Skill login.sh; no token inspection."""
from __future__ import annotations

import asyncio
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.services import baidu_drive_cli as cli
from app.services.baidu_drive_auth import BaiduDriveError, exclusive, read_private, user_dir, write_private

AUTH_MANAGEMENT_URL = "https://passport.baidu.com/v6/appAuthority"


def get_config(user_id: int) -> dict:
    return {"mode": "official-skill", "source": "baidu-netdisk/bdpan-storage", "root_path": cli.ROOT,
            "authorization_management_url": AUTH_MANAGEMENT_URL}


def account(user_id: int) -> dict | None:
    record = read_private(user_dir(user_id) / "official/active.json")
    if record is not None and (not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("account_id", "")))):
        raise BaiduDriveError("百度账号连接记录无效，请重新授权")
    return record


def _profile(raw: object) -> dict:
    if not isinstance(raw, dict) or raw.get("authenticated") is not True or raw.get("has_valid_token") is not True:
        raise BaiduDriveError("百度网盘尚未授权或授权已失效，请重新登录")
    result = {"nickname": str(raw.get("username") or "百度网盘账号")[:100]}
    expiration = raw.get("expires_at")
    if expiration:
        try:
            result["expires_at"] = int(datetime.fromisoformat(str(expiration).replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, OverflowError):
            pass
    return result


async def require_account(user_id: int) -> dict:
    record = account(user_id)
    if not record or not cli.config_path(user_id).is_file():
        raise BaiduDriveError("请先使用官方 Skill 登录授权百度网盘")
    return {**record, **_profile(await cli.run(user_id, "whoami"))}


async def get_status(user_id: int) -> dict:
    result = {"connected": False, "available": True, "verified": False, "auth_mode": "official-skill", "root_path": cli.ROOT}
    try:
        cli.binary()
    except BaiduDriveError as exc:
        return {**result, "available": False, "message": str(exc)}
    async with exclusive(user_id):
        record = account(user_id)
        if not record:
            return {**result, "message": "请使用官方 Skill 授权后管理「我的应用数据/bdpan」"}
        result.update({key: record[key] for key in ("account_id", "nickname", "expires_at") if key in record})
        try:
            result.update(_profile(await cli.run(user_id, "whoami")))
            result["connected"] = True
            # whoami only proves local token validity. A provider read verifies
            # that the saved authorization still permits real cloud access.
            probe = await cli.run(user_id, "ls", "--limit", "1", "--", cli.ROOT)
            if not isinstance(probe, list):
                raise BaiduDriveError("官方 CLI 未返回有效连接检查结果")
            result.update(verified=True, message="官方授权及文件访问已验证；管理范围：我的应用数据/bdpan")
        except BaiduDriveError as exc:
            result["message"] = str(exc)
        return result


@dataclass
class Session:
    owner: int
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "starting"
    authorization_url: str = ""
    expires_at: float = field(default_factory=lambda: time.time() + 600)
    message: str = "正在获取官方授权链接"
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    timer: asyncio.Task | None = None
    ready: asyncio.Future | None = None
    submitted: bool = False
    completion_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def directory(self) -> Path:
        return user_dir(self.owner) / "official/connections" / self.id

    def view(self) -> dict:
        return {"session_id": self.id, "status": self.status, "expires_at": int(self.expires_at * 1000),
                "authorization_url": self.authorization_url if self.status == "waiting" else "", "message": self.message}


_sessions: dict[str, Session] = {}
PENDING = {"starting", "waiting"}


def _get(user_id: int, session_id: str) -> Session:
    session = _sessions.get(session_id)
    if not session or session.owner != user_id:
        raise BaiduDriveError("授权会话已结束或无权访问，请重新开始授权")
    return session


async def _run(session: Session) -> None:
    try:
        configuration = session.directory / "config.json"
        cli.private_directory(session.directory)
        session.process = await cli.spawn("/bin/bash", str(cli.VENDOR / "skills/baidu-drive/scripts/login.sh"),
            "--continue-task", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=cli.child_env(configuration), start_new_session=True)
        assert session.process.stdin is not None and session.process.stdout is not None
        # This represents the explicit human checkbox in POST /auth/start, not an
        # agent silently skipping the official script's confirmation.
        session.process.stdin.write(b"y")
        await session.process.stdin.drain()
        size = 0
        while line := await session.process.stdout.readline():
            size += len(line)
            if size > 128 * 1024:
                raise BaiduDriveError("官方授权输出异常，请重新开始授权")
            text = line.decode("utf-8", errors="replace").strip()
            if session.status == "starting" and text.startswith("https://"):
                session.authorization_url = cli.trusted_url(text, authorization=True)
                session.status, session.message = "waiting", "请打开百度官方页面授权，将 32 位授权码粘贴回来"
                if session.ready and not session.ready.done():
                    session.ready.set_result(None)
        if await session.process.wait() != 0 or not session.submitted:
            raise BaiduDriveError("官方授权未完成，请检查授权码、网络或重新开始授权")
        profile = _profile(await cli.run(session.owner, "whoami", config=configuration))
        probe = await cli.run(session.owner, "ls", "--limit", "1", "--", cli.ROOT, config=configuration)
        if not isinstance(probe, list):
            raise BaiduDriveError("授权后未通过百度文件访问验证")
        if not configuration.is_file():
            raise BaiduDriveError("官方 CLI 未保存授权配置")
        configuration.chmod(0o600)
        async with exclusive(session.owner):
            if session.status != "waiting":
                raise BaiduDriveError("授权会话已结束")
            # Atomic pointer switch: a failed new login never replaces a valid
            # previous account. Tokens remain wholly owned by bdpan.
            previous = account(session.owner)
            write_private(user_dir(session.owner) / "official/active.json", {"account_id": session.id, **profile})
            session.status, session.message = "authorized", "官方 Skill 授权已保存"
            if previous and previous["account_id"] != session.id:
                old = user_dir(session.owner) / "official/connections" / previous["account_id"] / "config.json"
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    session.message = "新账号授权已保存；旧连接凭证清理失败，请检查私有目录权限"
    except asyncio.CancelledError:
        if session.status in PENDING:
            session.status, session.message = "cancelled", "已取消授权"
        raise
    except (BaiduDriveError, OSError, ValueError):
        session.status, session.message = "failed", "官方授权失败，请检查授权码、网络或重新开始授权"
    finally:
        if session.process:
            await cli.terminate(session.process)
        if session.status != "authorized":
            shutil.rmtree(session.directory, ignore_errors=True)
        if session.ready and not session.ready.done():
            session.ready.set_result(None)
        if session.timer and session.status not in PENDING:
            session.timer.cancel()


async def _expire(session: Session):
    await asyncio.sleep(max(0, session.expires_at - time.time()))
    if session.status in PENDING:
        session.status, session.message = "expired", "授权链接已过期，请重新开始授权"
        if session.task:
            session.task.cancel()


async def start_authorization(user_id: int, confirmed: bool) -> dict:
    if confirmed is not True:
        raise BaiduDriveError("请先阅读并确认官方 Skill 安全须知")
    async with exclusive(user_id):
        cli.binary()
        for old in list(_sessions.values()):
            if old.owner == user_id and old.status in PENDING:
                await _cancel(old)
            if old.expires_at < time.time() - 600:
                if old.timer:
                    old.timer.cancel()
                _sessions.pop(old.id, None)
        if len(_sessions) >= 100:
            raise BaiduDriveError("授权请求过多，请稍后重试")
        session = Session(user_id)
        session.ready = asyncio.get_running_loop().create_future()
        _sessions[session.id] = session
        session.task = asyncio.create_task(_run(session))
        session.timer = asyncio.create_task(_expire(session))
        try:
            await asyncio.wait_for(asyncio.shield(session.ready), 25)
        except asyncio.TimeoutError:
            await _cancel(session)
            raise BaiduDriveError("获取官方授权链接超时，请检查网络后重试")
        if session.status != "waiting":
            raise BaiduDriveError(session.message)
        return {**session.view(), "credential_type": "authorization_code", "mode": "official-skill"}


async def complete_authorization(user_id: int, credential: str, credential_type: str, session_id: str) -> dict:
    if credential_type != "authorization_code" or not re.fullmatch(r"[a-fA-F0-9]{32}", credential):
        raise BaiduDriveError("请输入百度官方页面提供的 32 位十六进制授权码")
    session = _get(user_id, session_id)
    async with session.completion_lock:
        if session.status != "waiting" or session.expires_at <= time.time() or session.submitted:
            raise BaiduDriveError("授权会话已结束或授权码已提交，请重新开始授权")
        if session.process is None or session.process.stdin is None:
            raise BaiduDriveError("授权进程已结束，请重新开始授权")
        session.submitted = True
        try:
            session.process.stdin.write((credential + "\n").encode())
            await session.process.stdin.drain()
            await asyncio.wait_for(asyncio.shield(session.task), 60)
        except (BrokenPipeError, ConnectionError, asyncio.TimeoutError) as exc:
            await _cancel(session)
            raise BaiduDriveError("授权未完成，请检查连接后重新开始授权") from exc
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            raise BaiduDriveError(session.message)
        if session.status != "authorized":
            raise BaiduDriveError(session.message)
        return await get_status(user_id)


async def _cancel(session: Session) -> None:
    if session.status in PENDING:
        session.status, session.message = "cancelled", "已取消授权"
        if session.task:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
    if session.timer:
        session.timer.cancel()


async def cancel(user_id: int, session_id: str) -> dict:
    if session_id not in _sessions and re.fullmatch(r"[0-9a-f]{32}", session_id):
        return {"session_id": session_id, "status": "cancelled", "expires_at": 0,
                "authorization_url": "", "message": "授权会话已结束"}
    session = _get(user_id, session_id)
    # Cancellation must interrupt an in-flight code exchange, not queue behind it.
    await _cancel(session)
    return session.view()


async def session_status(user_id: int, session_id: str) -> dict:
    return _get(user_id, session_id).view()


async def shutdown() -> None:
    for session in list(_sessions.values()):
        await _cancel(session)


async def logout(user_id: int) -> dict:
    async with exclusive(user_id):
        for session in list(_sessions.values()):
            if session.owner == user_id:
                await _cancel(session)
        if account(user_id):
            configuration = cli.config_path(user_id)
            await cli.run(user_id, "logout")
            configuration.unlink(missing_ok=True)
            (user_dir(user_id) / "official/active.json").unlink(missing_ok=True)
    return {"connected": False, "authorization_management_url": AUTH_MANAGEMENT_URL,
            "message": "已清除项目当前账号的本地授权；彻底撤销应用权限请前往百度账号授权管理"}
