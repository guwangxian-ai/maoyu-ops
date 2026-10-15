"""Short-lived web authorization sessions using the official CLI's OAuth polling."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.quark_drive_auth import (
    QuarkAuthError, _extract_auth_link, _has_account, _record_legacy_account,
    _run_cli, _user_locks, complete_authorization,
)


@dataclass
class AuthorizationSession:
    id: str
    owner_id: int
    status: str = "starting"
    authorization_url: str = ""
    expires_at: float = 0
    message: str = "正在连接夸克官方授权服务"
    created_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None
    ready: asyncio.Future | None = None


_sessions: dict[str, AuthorizationSession] = {}
_session_gates: dict[int, asyncio.Lock] = {}
PENDING = {"starting", "waiting"}


def _view(session: AuthorizationSession) -> dict:
    return {"session_id": session.id, "status": session.status,
            "authorization_url": session.authorization_url if session.status == "waiting" else "",
            "expires_at": int(session.expires_at * 1000), "message": session.message}


def _get(user_id: int, session_id: str) -> AuthorizationSession:
    session = _sessions.get(session_id)
    if session is None or session.owner_id != user_id:
        raise QuarkAuthError("授权会话已结束或无权访问，请重新获取二维码")
    return session


async def _assert_no_jobs(user_id: int) -> None:
    from sqlalchemy import select
    from common.db.session import async_session_maker
    from common.models.cloud_drive_job import CloudDriveJob

    async with async_session_maker() as database:
        job = await database.scalar(select(CloudDriveJob.id).where(
            CloudDriveJob.owner_id == user_id, CloudDriveJob.provider == "quark",
            CloudDriveJob.status.in_(("queued", "running"))).limit(1))
        if job:
            raise QuarkAuthError("当前还有网盘任务等待或执行，请完成或停止任务后再登录或切换账号")


async def _run(session: AuthorizationSession) -> None:
    def ready() -> None:
        if session.ready is not None and not session.ready.done():
            session.ready.set_result(None)

    async def on_event(event: dict) -> None:
        if event.get("type") != "authorization" or session.status != "starting":
            return
        url = _extract_auth_link(str((event.get("data") or {}).get("authorization_url") or ""))
        if not url:
            raise QuarkAuthError("夸克未返回有效的官方授权地址")
        session.authorization_url = url
        session.expires_at = time.time() + 60  # the pinned CLI polls for 60 seconds
        session.status = "waiting"
        session.message = "请扫码并在夸克确认授权"
        ready()

    try:
        async with _user_locks.setdefault(session.owner_id, asyncio.Lock()):
            result = await _run_cli(session.owner_id, "login", timeout=90,
                                    on_event=on_event, browser_bridge=True)
            if result.get("code") in (0, -118) and _has_account(session.owner_id):
                _record_legacy_account(session.owner_id)
                session.status = "authorized"
                session.message = "夸克授权已保存"
            else:
                session.status = "expired" if session.authorization_url else "failed"
                session.message = "授权未完成，请刷新二维码或打开官方授权页后使用授权码"
    except asyncio.CancelledError:
        session.status = "cancelled"
        session.message = "已取消等待授权"
    except QuarkAuthError:
        session.status = "failed"
        session.message = "夸克授权连接失败，请重试或使用官方授权页"
    except Exception:
        session.status = "failed"
        session.message = "无法启动夸克授权，请稍后重试"
    finally:
        ready()


async def start_session(user_id: int) -> dict:
    async with _session_gates.setdefault(user_id, asyncio.Lock()):
        # A deliberate refresh retires the previous wait before issuing a new URL.
        for session in list(_sessions.values()):
            if session.owner_id == user_id and session.status in PENDING:
                await cancel_session(user_id, session.id)
        if _user_locks.setdefault(user_id, asyncio.Lock()).locked():
            raise QuarkAuthError("当前网盘任务正在执行，请完成后再授权")
        await _assert_no_jobs(user_id)
        for identity, old in list(_sessions.items()):
            if old.status not in PENDING and time.time() - old.created_at > 600:
                del _sessions[identity]
        session = AuthorizationSession(id=uuid4().hex, owner_id=user_id,
                                       ready=asyncio.get_running_loop().create_future())
        _sessions[session.id] = session
        session.task = asyncio.create_task(_run(session))
        try:
            await asyncio.wait_for(asyncio.shield(session.ready), timeout=25)
        except asyncio.TimeoutError:
            await cancel_session(user_id, session.id)
            raise QuarkAuthError("连接夸克授权服务超时，请稍后重试")
        if session.status in {"failed", "cancelled"}:
            raise QuarkAuthError(session.message)
        return _view(session)


async def get_session(user_id: int, session_id: str) -> dict:
    return _view(_get(user_id, session_id))


async def cancel_session(user_id: int, session_id: str) -> dict:
    session = _get(user_id, session_id)
    if session.task is not None and not session.task.done() and session.status in PENDING:
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        # A task cancelled before its first instruction never reaches finally.
        if session.status in PENDING:
            session.status, session.message = "cancelled", "已取消等待授权"
    return _view(session)


async def complete_session(user_id: int, session_id: str, code: str) -> dict:
    session = _get(user_id, session_id)
    if session.status == "authorized":
        return {"connected": True}
    await cancel_session(user_id, session_id)
    result = await complete_authorization(user_id, code)
    session.status, session.message = "authorized", "夸克授权已保存"
    return result


async def stop_sessions() -> None:
    for session in list(_sessions.values()):
        if session.status in PENDING:
            await cancel_session(session.owner_id, session.id)
