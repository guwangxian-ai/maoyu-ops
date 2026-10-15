"""Website-authenticated MCP credential management, audit and private files."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select

from app.api import deps
from app.mcp import files, onboarding
from app.mcp.auth import PersonalTokenVerifier, SCOPES, issue_token, owner_user, token_metadata, utcnow
from app.mcp.server import builtin_tools
from app.mcp.permissions import can_upload
from common.models.mcp_access import McpAccessToken, McpToolLog
from common.models.user import UserStatus


def private(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/mcp", tags=["MCP 接入"], dependencies=[Depends(private)])


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=64)
    expires_in_days: int = Field(default=30, ge=1, le=90)


class ConnectionCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=256)


@router.get("/connection")
async def connection(user=Depends(deps.get_current_active_user)):
    try:
        return {"success": True, "data": onboarding.connection()}
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/connection/check")
async def check_connection(body: ConnectionCheckRequest, request: Request, user=Depends(deps.get_current_active_user)):
    access = await PersonalTokenVerifier().verify_token(body.token.get_secret_value())
    if access is None or access.subject != str(user.id):
        raise HTTPException(400, "密钥无效、已过期、已撤销或不属于当前用户")
    gateway = request.app.state.mcp_gateway
    return {"success": True, "data": {"valid": True, "scopes": [s for s in access.scopes if s != "mcp"],
            "tool_count": len(gateway.metadata(access.scopes)) + len(builtin_tools(access)),
            "checked_at": utcnow().isoformat() + "Z",
            "message": "密钥有效，权限已核对；请在 AI 客户端完成 MCP 连接。"}}


@router.get("/catalog")
async def catalog(request: Request, user=Depends(deps.get_current_active_user)):
    gateway = request.app.state.mcp_gateway
    return {"success": True, "data": {"endpoint": "/mcp", "transport": "streamable-http", "auth": "personal-bearer-token",
            "scopes": sorted(SCOPES), "tools": gateway.metadata()}}


@router.post("/tokens")
async def create_token(body: TokenRequest, user=Depends(deps.get_current_active_user), session=Depends(deps.get_db_session)):
    try:
        row, token = await issue_token(session, user.id, body.name.strip(), body.scopes, body.expires_in_days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"success": True, "data": {**token_metadata(row), "token": token, "message": "令牌只显示一次，请保存在 AI 客户端的私有配置中"}}


@router.get("/tokens")
async def tokens(user=Depends(deps.get_current_active_user), session=Depends(deps.get_db_session)):
    rows = (await session.scalars(select(McpAccessToken).where(McpAccessToken.owner_id == user.id).order_by(McpAccessToken.created_at.desc()))).all()
    last_used = dict((await session.execute(select(McpToolLog.token_id, func.max(McpToolLog.created_at))
                     .where(McpToolLog.owner_id == user.id).group_by(McpToolLog.token_id))).all())
    return {"success": True, "data": [{**token_metadata(row), "last_used_at": last_used.get(row.id)} for row in rows]}


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, user=Depends(deps.get_current_active_user), session=Depends(deps.get_db_session)):
    row = await session.get(McpAccessToken, token_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(404, "令牌不存在")
    row.revoked_at = utcnow()
    await session.commit()
    return {"success": True}


@router.get("/logs")
async def logs(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
               user=Depends(deps.get_current_active_user), session=Depends(deps.get_db_session)):
    rows = (await session.scalars(select(McpToolLog).where(McpToolLog.owner_id == user.id)
                                  .order_by(McpToolLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    total = await session.scalar(select(func.count()).select_from(McpToolLog).where(McpToolLog.owner_id == user.id))
    return {"success": True, "data": [{"id": r.id, "token_id": r.token_id, "tool": r.tool_name, "success": r.success,
            "duration_ms": r.duration_ms, "error_code": r.error_code, "created_at": r.created_at} for r in rows],
            "total": total or 0, "page": page, "page_size": page_size}


async def file_identity(authorization: str = Header(default=""), session=Depends(deps.get_db_session)):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(401, "需要 Bearer 令牌")
    if token.startswith("xy_mcp_"):
        access = await PersonalTokenVerifier().verify_token(token)
        if access is None:
            raise HTTPException(401, "MCP 令牌无效或已过期")
        return await owner_user(access), access.scopes, access
    user = await deps.get_current_user(token=token, session=session)
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(403, "账号不可用")
    return user, SCOPES, None


@router.post("/files")
async def upload_file(request: Request, file: UploadFile = File(...), identity=Depends(file_identity),
                      operation_id: str | None = Header(default=None, alias="X-MCP-Operation-Id")):
    user, scopes, access = identity
    if not can_upload(scopes):
        raise HTTPException(403, "上传需要写入权限")
    digest, size = hashlib.sha256(), 0
    while chunk := await file.read(65536):
        size += len(chunk)
        if size > 256 * 1024 * 1024:
            raise HTTPException(413, "暂存文件不能超过256 MiB")
        digest.update(chunk)
    await file.seek(0)
    async def chunks():
        while chunk := await file.read(65536):
            yield chunk
    try:
        async def invoke():
            return {"success": True, "data": await files.store_stream(user.id, file.filename or "file.bin", file.content_type, chunks())}
        if access:
            return await request.app.state.mcp_gateway.operations.run(access, operation_id, "mcp_http_upload", "mcp:upload",
                {"filename": file.filename or "file.bin", "size": size, "sha256": digest.hexdigest(), "content_type": file.content_type}, invoke)
        return await invoke()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/files/{identifier}/content")
async def download_file(identifier: str, identity=Depends(file_identity)):
    user, _, _ = identity
    try:
        record, blob = files.lookup(user.id, identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(blob, filename=record["filename"], media_type="application/octet-stream", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
