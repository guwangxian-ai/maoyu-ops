"""Authenticated Baidu Drive APIs for the UI and future AI adapters."""
from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.services import baidu_pcs_drive as drive
from app.services import baidu_pcs_auth as auth
from common.models.user import User
from common.schemas.common import ApiResponse


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/cloud-drive/baidu", tags=["百度网盘"], dependencies=[Depends(private_response)])


class PrivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigRequest(PrivateRequest):
    mode: Literal["experience", "application"]
    client_id: str = Field(default="", max_length=256)
    client_secret: str = Field(default="", max_length=4096, repr=False)


class CompleteAuthorizationRequest(PrivateRequest):
    credential: str = Field(pattern=r"^[a-fA-F0-9]{32}$", repr=False)
    credential_type: Literal["authorization_code"] = "authorization_code"
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ConfirmedRequest(PrivateRequest):
    confirmed: Literal[True]


class ShareRequest(ConfirmedRequest):
    fsids: list[str] = Field(default_factory=list, max_length=100)
    paths: list[str] = Field(default_factory=list, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    period: Literal[0, 1, 7, 30] = 7
    password: str = Field(default="", max_length=4)


class OperationRequest(PrivateRequest):
    paths: list[str] = Field(default_factory=list, max_length=100)
    fsids: list[str] = Field(default_factory=list, max_length=100)
    target_dir: str | None = Field(default=None, max_length=4096)
    dir: str = Field(default="/", max_length=4096)
    name: str | None = Field(default=None, max_length=255)
    query: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=20000)
    filename: str | None = Field(default=None, max_length=255)
    confirmed: bool = False
    password: str = Field(default="", max_length=4, repr=False)
    source_dir: str | None = Field(default=None, max_length=4096)
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=100, ge=1, le=100)


async def _reply(result: Awaitable[dict]) -> ApiResponse:
    try:
        return ApiResponse(success=True, data=await result)
    except auth.BaiduDriveError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/capabilities", response_model=ApiResponse)
async def capabilities(user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return ApiResponse(success=True, data={"provider": "baidu", "source": "qjfoidnh/BaiduPCS-Go",
        "transport": "private JSON bridge", "upstream_version": "4.0.2-dev", "root_path": "/",
        "operations": [*drive.OPERATIONS, "upload", "download", "share", "cancel-share"],
        "additional": ["qrcode-authorization", "logout", "files", "keyword-search", "account-shares", "share-detail", "quota"],
        "unsupported": {"semantic-search": "关键词搜索由百度提供；文件内容分析由 AI 完成",
                        "upload-url": "当前接入支持本地文件或文本上传",
                        "share-transfer": "分享转存接口尚未接入当前桥接",
                        "share-transfer-list": "分享目录预览接口尚未接入当前桥接",
                        "share-transfer-select": "分享选择转存尚未接入当前桥接",
                        "directory-download": "当前下载接口支持普通文件，请先在网盘打包目录"},
        "notices": {"authorization": "扫码登录由用户确认，凭证仅保存在当前项目服务端",
                    "share": "返回百度原生分享链接；可用性受百度账号和平台规则影响"},
        "limits": {"selected_files": 100, "upload_files": 100, "search_page_size": 50,
                   "upload_bytes": int(os.environ.get("BAIDU_MAX_UPLOAD_BYTES", str(1024**3))),
                   "download_bytes": int(os.environ.get("BAIDU_MAX_DOWNLOAD_BYTES", str(256 * 1024**2)))}})


@router.get("/status", response_model=ApiResponse)
async def status(user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(auth.get_status(int(user.id)))


@router.get("/auth/config", response_model=ApiResponse)
async def config(user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        return ApiResponse(success=True, data=auth.get_config(int(user.id)))
    except auth.BaiduDriveError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.put("/auth/config", response_model=ApiResponse)
async def save_config(payload: ConfigRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return ApiResponse(success=False, message="已使用 BaiduPCS-Go，请在当前页面扫码登录，无需配置 AppKey / SecretKey")


@router.post("/auth/start", response_model=ApiResponse)
async def auth_start(payload: ConfirmedRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(auth.start_authorization(int(user.id), payload.confirmed))


@router.post("/auth/complete", response_model=ApiResponse)
async def auth_complete(payload: CompleteAuthorizationRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return ApiResponse(success=False, message="当前接入通过扫码会话自动完成登录，不再接受旧 Skill 授权码")


@router.get("/auth/sessions/{session_id}", response_model=ApiResponse)
async def auth_session(session_id: str, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(auth.session_status(int(user.id), session_id))


@router.post("/auth/sessions/{session_id}/cancel", response_model=ApiResponse)
async def auth_cancel(session_id: str, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(auth.cancel(int(user.id), session_id))


@router.post("/auth/revoke", response_model=ApiResponse)
async def auth_revoke(payload: ConfirmedRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(auth.logout(int(user.id)))


@router.get("/files", response_model=ApiResponse)
async def files(dir: str = Query(default="/", max_length=4096), keyword: str = Query(default="", max_length=100),
                page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=100, ge=1, le=100),
                category: Literal["all", "video", "audio", "image", "doc", "app", "other", "folder"] = "all",
                user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(drive.list_files(int(user.id), dir, keyword, page, page_size, category))


@router.get("/shares", response_model=ApiResponse)
async def shares(page: int = Query(default=1, ge=1, le=10000),
                 user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(drive.list_shares(int(user.id), page))


@router.post("/shares", response_model=ApiResponse)
async def create_share(payload: ShareRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(drive.create_share(int(user.id), payload.fsids, payload.title, payload.period, payload.password, payload.paths))


@router.get("/shares/{share_id}", response_model=ApiResponse)
async def share_detail(share_id: str, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(drive.share_detail(int(user.id), share_id))


@router.post("/shares/{share_id}/cancel", response_model=ApiResponse)
async def cancel_share(share_id: str, payload: ConfirmedRequest,
                       user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return await _reply(drive.cancel_share(int(user.id), share_id, payload.confirmed))


@router.post("/operations/{operation}", response_model=ApiResponse)
async def operation(operation: Literal["create-folder", "copy", "move", "rename", "delete", "file-meta", "semantic-search", "upload-url", "upload-text", "share-transfer", "share-transfer-list", "share-transfer-select"],
                    payload: OperationRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    result = await _reply(drive.run_operation(int(user.id), operation, payload.model_dump(exclude_none=True)))
    if result.success and isinstance(result.data, dict) and result.data.get("status") == "failed":
        result.success = False
        result.message = result.data.get("message", "操作失败")
    return result


@router.get("/download")
async def download(path: str = Query(max_length=4096), user: User = Depends(deps.get_current_active_user)):
    try:
        file, name, directory = await drive.download(int(user.id), path)
        return FileResponse(file, filename=name, media_type="application/octet-stream", headers={"Cache-Control": "no-store"},
                            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True))
    except auth.BaiduDriveError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/upload", response_model=ApiResponse)
async def upload(files: list[UploadFile] = File(...), dir: str = Form(default="/"),
                 user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        data = await drive.upload_files(int(user.id), files, dir)
        return ApiResponse(success=data["status"] != "failed", data=data, message=data["message"])
    except auth.BaiduDriveError as exc:
        return ApiResponse(success=False, message=str(exc))
    finally:
        for file in files:
            await file.close()
