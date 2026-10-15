"""Durable, owner-scoped write receipts. Unknown effects are never retried.

The unique database key arbitrates across processes and MCP credentials. A
lease only detects abandoned work; it never authorizes another execution.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import hashlib
import json
import re
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.mcp.auth import utcnow
from app.mcp.permissions import SCOPES, can_upload, permits
from common.models.mcp_access import McpOperation

OPERATION_PATTERN = r"^[a-z0-9][a-z0-9:._/-]{0,159}$"
OPERATION_SCHEMA = {"type": "string", "pattern": OPERATION_PATTERN,
                    "description": "Stable, owner-wide operation ID. Reuse the SAME ID and parameters after interruption; never invent a new ID to retry unknown effects."}
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20


def fingerprint(tool, arguments):
    business = {k: v for k, v in arguments.items() if k != "operation_id"}
    return hashlib.sha256(json.dumps([tool, business], ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def readable(access, row):
    return (can_upload(access.scopes) if row.required_scope == "mcp:upload" else
            permits(access.scopes, row.required_scope) or row.required_scope.split(":")[0] + ":read" in access.scopes)


def metadata(row):
    def stamp(value):
        return value.isoformat() + "Z" if value else None
    return {"operation_id": row.operation_id, "tool": row.tool_name, "state": row.state,
            "required_scope": row.required_scope, "created_at": stamp(row.created_at),
            "finished_at": stamp(row.finished_at), "lease_expires_at": stamp(row.lease_expires_at),
            "retry_allowed": False}


def response_for(row, replayed=True):
    operation = {**metadata(row), "replayed": replayed}
    if row.state == "observed" and row.response is not None:
        return {**row.response, "mcp_operation": operation}
    return {"success": False, "code": "OPERATION_IN_PROGRESS" if row.state == "running" else "OPERATION_UNCERTAIN",
            "message": "原操作仍在执行，请查询同一编号" if row.state == "running" else "原操作结果未知；先查询任务或平台状态，不得换编号重发",
            "mcp_operation": operation}


class OperationStore:
    def __init__(self, session_factory):
        self.sessions = session_factory
        self.tasks: set[asyncio.Task] = set()

    async def _load(self, session, owner_id, operation_id):
        row = (await session.execute(select(McpOperation).where(
            McpOperation.owner_id == owner_id, McpOperation.operation_id == operation_id))).scalar_one_or_none()
        if row and row.state == "running" and row.lease_expires_at and row.lease_expires_at < utcnow():
            # Conditional transition avoids overwriting a concurrent completion.
            await session.execute(update(McpOperation).where(
                McpOperation.id == row.id, McpOperation.state == "running",
                McpOperation.lease_expires_at < utcnow()).values(state="uncertain"))
            await session.commit()
            await session.refresh(row)
        return row

    async def get(self, access, operation_id):
        async with self.sessions() as session:
            row = await self._load(session, int(access.subject), operation_id)
            if row is None or not readable(access, row):
                return {"success": False, "code": "OPERATION_NOT_FOUND", "message": "当前用户和权限下没有此服务端回执；历史客户端回执仍须单独核对"}
            return {"success": True, "data": {**metadata(row), "request_hash": row.request_hash,
                    "result": row.response, "recovery": "observed 是已收到业务响应；异步任务仍需查询其状态。running 继续查询；uncertain 只读对账，原编号不会再次执行。"}}

    async def listing(self, access, limit=30, before=None):
        async with self.sessions() as session:
            # Filter before limiting so a token cannot infer inaccessible records.
            visible_scopes = [scope for scope in SCOPES if permits(access.scopes, scope)
                              or scope.split(":")[0] + ":read" in access.scopes]
            if can_upload(access.scopes):
                visible_scopes.append("mcp:upload")
            stmt = select(McpOperation).where(McpOperation.owner_id == int(access.subject),
                                              McpOperation.required_scope.in_(visible_scopes))
            if before:
                anchor = await self._load(session, int(access.subject), before)
                if anchor is None or not readable(access, anchor):
                    return {"success": False, "code": "OPERATION_NOT_FOUND"}
                stmt = stmt.where(or_(McpOperation.created_at < anchor.created_at,
                    and_(McpOperation.created_at == anchor.created_at, McpOperation.id < anchor.id)))
            rows = (await session.scalars(stmt.order_by(McpOperation.created_at.desc(), McpOperation.id.desc()).limit(limit + 1))).all()
            selected = rows[:limit]
            values = []
            for row in selected:
                values.append(metadata(await self._load(session, int(access.subject), row.operation_id)))
            return {"success": True, "data": {"operations": values,
                    "next_cursor": selected[-1].operation_id if len(rows) > limit else None}}

    async def run(self, access, operation_id, tool, scope, arguments, invoke):
        if not isinstance(operation_id, str) or not re.fullmatch(OPERATION_PATTERN, operation_id):
            return {"success": False, "code": "OPERATION_ID_REQUIRED", "message": "写入需要稳定 operation_id（小写字母、数字和 :._/-，最多160字符）"}
        digest = fingerprint(tool, arguments)
        async with self.sessions() as session:
            row = McpOperation(id=uuid4().hex, owner_id=int(access.subject), token_id=access.client_id,
                operation_id=operation_id, tool_name=tool, required_scope=scope, request_hash=digest,
                state="running", lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS),
                created_at=utcnow(), updated_at=utcnow())
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._load(session, int(access.subject), operation_id)
                if existing is None:
                    raise
                if existing.request_hash != digest or existing.tool_name != tool:
                    return {"success": False, "code": "OPERATION_CONFLICT", "message": "同一操作编号已有不同工具或参数，未执行新操作"}
                return response_for(existing)
            await session.refresh(row)
            identifier = row.id
        task = asyncio.create_task(self._run_reserved(identifier, invoke))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # Client disconnection must not cancel an already dispatched mutation.
        return await asyncio.shield(task)

    async def _heartbeat(self, identifier):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with self.sessions() as session:
                    await session.execute(update(McpOperation).where(
                        McpOperation.id == identifier, McpOperation.state == "running"
                    ).values(lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS)))
                    await session.commit()
            except Exception:
                # Lost lease never permits takeover. The original result can
                # still be committed; until then readers see an uncertain state.
                return

    async def _run_reserved(self, identifier, invoke):
        heartbeat = asyncio.create_task(self._heartbeat(identifier))
        response, state = None, "uncertain"
        try:
            response = await invoke()
            code = str(response.get("code", ""))
            state = "uncertain" if code in {"OPERATION_FAILED", "INTERRUPTED_CHECK_STATUS", "INVALID_RESPONSE"} or (code.isdigit() and 500 <= int(code) < 600) else "observed"
        except asyncio.CancelledError:
            raise
        except Exception:
            response = {"success": False, "code": "OPERATION_UNCERTAIN", "message": "执行异常；先核对原任务，不得重复发送"}
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            try:
                async with self.sessions() as session:
                    row = await session.get(McpOperation, identifier)
                    row.state, row.response, row.finished_at, row.lease_expires_at = state, response, utcnow(), None
                    await session.commit()
                    await session.refresh(row)
                    saved = response_for(row, replayed=False)
            except Exception:
                # Never claim that a successful side effect failed and is safe
                # to retry merely because its receipt could not be persisted.
                saved = {**(response or {"success": False}), "code": "RECEIPT_PERSISTENCE_UNCERTAIN",
                         "message": "业务可能已执行，但回执未确认保存；查询原操作和平台状态，禁止重发"}
        return saved

    async def close(self):
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=20)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
