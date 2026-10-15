"""Personal access tokens for explicitly configured, private MCP clients.

These are not website sessions or an OAuth authorization server. Tokens are
hashed at rest, checked on every HTTP request, and never confer admin access.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from mcp.server.auth.provider import AccessToken, TokenVerifier
from common.db.session import async_session_maker
from common.models.mcp_access import McpAccessToken
from common.models.user import User, UserRole, UserStatus
from app.mcp.permissions import SCOPES

TOKEN_PATTERN = re.compile(r"^xy_mcp_([a-f0-9]{32})\.[A-Za-z0-9_-]{43}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def issue_token(session, owner_id: int, name: str, scopes: list[str], days: int):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("令牌名称需要 1–80 个字符")
    if not scopes or set(scopes) - SCOPES or not 1 <= days <= 90:
        raise ValueError("请选择有效权限和 1–90 天有效期")
    identifier = uuid4().hex
    token = f"xy_mcp_{identifier}.{secrets.token_urlsafe(32)}"
    row = McpAccessToken(id=identifier, owner_id=owner_id, name=name,
                         token_hash=hashlib.sha256(token.encode()).hexdigest(),
                         scopes=sorted(set(scopes)), expires_at=utcnow() + timedelta(days=days))
    session.add(row)
    await session.commit()
    # MySQL does not RETURNING server-generated timestamps. Load them explicitly
    # before serializing metadata instead of triggering implicit async ORM I/O.
    await session.refresh(row)
    return row, token


def token_metadata(row: McpAccessToken) -> dict:
    return {"id": row.id, "name": row.name, "scopes": row.scopes,
            "expires_at": row.expires_at.isoformat() + "Z",
            "revoked": row.revoked_at is not None,
            "created_at": row.created_at,
            "revoked_at": row.revoked_at.isoformat() + "Z" if row.revoked_at else None}


class PersonalTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        match = TOKEN_PATTERN.fullmatch(token)
        if not match:
            return None
        async with async_session_maker() as session:
            row = await session.get(McpAccessToken, match.group(1))
            if (row is None or row.revoked_at is not None or row.expires_at <= utcnow()
                    or not hmac.compare_digest(row.token_hash, hashlib.sha256(token.encode()).hexdigest())):
                return None
            user = await session.get(User, row.owner_id)
            if user is None or user.status != UserStatus.ACTIVE:
                return None
            return AccessToken(token=token, client_id=row.id, subject=str(user.id),
                               scopes=["mcp", *row.scopes],
                               expires_at=int(row.expires_at.replace(tzinfo=timezone.utc).timestamp()))


async def owner_user(access: AccessToken) -> User:
    """Return an unprivileged snapshot; never mutate the real user's ORM row."""
    async with async_session_maker() as session:
        user = await session.get(User, int(access.subject or "0"))
        if user is None or user.status != UserStatus.ACTIVE:
            raise ValueError("账号不可用")
        return User(id=user.id, username=user.username, email=user.email,
                    role=UserRole.MEMBER, status=UserStatus.ACTIVE,
                    account_limit=user.account_limit)
