"""Per-user Quark Drive authorization, file and share endpoints."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api import deps
from app.services.quark_drive_auth import (
    QuarkAuthError,
    complete_authorization,
    get_status,
    start_authorization,
    start_unauthorization,
)
from common.models.user import User
from common.schemas.common import ApiResponse
from app.services.quark_drive import create_share, list_files, list_shares
from app.services.quark_drive_operations import Operation, OperationRequest, READ_OPERATIONS, build_arguments, execute_query
from app.services.quark_drive_jobs import (
    cancel_job, create_followup, create_job, download_file_path, get_job, job_directory, job_view,
    list_jobs, public_data, queue_plan, run_job,
)
from app.services.quark_drive_plans import RenamePlanRequest, prepare_batch_organize, prepare_rename
from app.services.quark_drive_sessions import start_session, get_session, cancel_session, complete_session, _assert_no_jobs


router = APIRouter(prefix="/cloud-drive/quark", tags=["夸克网盘"])


@router.get("/capabilities", response_model=ApiResponse)
async def quark_capabilities(user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    return ApiResponse(success=True, data={"provider": "quark", "version": "1.0.22",
        "operations": list(Operation.__args__),
        "additional": ["authorization", "unauthorize", "share", "upload", "rename", "rename-revert",
                       "batch-organize", "batch-organize-resume", "batch-organize-undo"],
        "unsupported": {"delete-files": "官方 Skill 未提供删除云端文件的命令", "all-shares": "官方 Skill 未提供账号全部历史分享查询"},
        "limits": {"selected_files": 100, "rename_items": 5000, "upload_files": 1000,
                   "upload_bytes": int(os.environ.get("QUARK_MAX_UPLOAD_BYTES", str(1024**3)))}})


class ConfirmedRequest(BaseModel):
    confirmed: Literal[True]


class BatchPlanRequest(BaseModel):
    proposal: dict
    target_label: str | None = Field(default=None, max_length=1024)


class FollowupRequest(ConfirmedRequest):
    action: Literal["revert", "rename-resume", "resume", "undo", "undo-resume"]


@router.post("/auth/revoke", response_model=ApiResponse)
async def quark_revoke(payload: ConfirmedRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        await _assert_no_jobs(int(user.id))
        return ApiResponse(success=True, data=await start_unauthorization(int(user.id)))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


class CompleteAuthorizationRequest(BaseModel):
    code: str = Field(min_length=8, max_length=2048)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


@router.post("/auth/sessions", response_model=ApiResponse)
async def quark_auth_session_start(response: Response, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await start_session(int(user.id)))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/auth/sessions/{session_id}", response_model=ApiResponse)
async def quark_auth_session_status(session_id: str, response: Response, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await get_session(int(user.id), session_id))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/auth/sessions/{session_id}/cancel", response_model=ApiResponse)
async def quark_auth_session_cancel(session_id: str, response: Response, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await cancel_session(int(user.id), session_id))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


class CreateShareRequest(BaseModel):
    fids: list[str] = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    url_type: Literal[1, 2] = 2
    expired_type: Literal[1, 2, 3, 4, 5, 6, 7] = 1


@router.get("/status", response_model=ApiResponse)
async def quark_status(
    response: Response,
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await get_status(int(user.id)))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/auth/start", response_model=ApiResponse)
async def quark_auth_start(
    response: Response,
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await start_authorization(int(user.id)))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/auth/complete", response_model=ApiResponse)
async def quark_auth_complete(
    payload: CompleteAuthorizationRequest,
    response: Response,
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        await _assert_no_jobs(int(user.id))
        data = await complete_session(int(user.id), payload.session_id, payload.code) if payload.session_id else await complete_authorization(int(user.id), payload.code)
        return ApiResponse(success=True, data=data)
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/files", response_model=ApiResponse)
async def quark_files(
    response: Response,
    parent_fid: str = Query(default="0", min_length=1, max_length=2048),
    keyword: str = Query(default="", max_length=50),
    search_type: Literal["mix", "video", "album", "doc", "audio", "dir", "package", "other", "app"] = "mix",
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await list_files(int(user.id), parent_fid, keyword, search_type))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/shares", response_model=ApiResponse)
async def quark_shares(
    response: Response,
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data=await list_shares(int(user.id)))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/shares", response_model=ApiResponse)
async def quark_create_share(
    payload: CreateShareRequest,
    response: Response,
    user: User = Depends(deps.get_current_active_user),
) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        data = await create_share(int(user.id), payload.fids, payload.title,
                                  payload.url_type, payload.expired_type)
        return ApiResponse(success=True, data=data)
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/operations/{operation}", response_model=ApiResponse)
async def quark_operation(operation: Operation, payload: OperationRequest, background_tasks: BackgroundTasks,
                          response: Response, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        build_arguments(operation, payload)
        if operation in READ_OPERATIONS:
            return ApiResponse(success=True, data=public_data(await execute_query(int(user.id), operation, payload)))
        job = await create_job(int(user.id), operation, payload.model_dump())
        background_tasks.add_task(run_job, int(user.id), job["id"])
        return ApiResponse(success=True, data={"job": job})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


def safe_upload_name(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise QuarkAuthError("上传文件路径无效")
    if len(value) > 1024 or any(len(part.encode("utf-8")) > 255 for part in path.parts):
        raise QuarkAuthError("上传文件名或目录路径过长")
    return path


@router.post("/upload", response_model=ApiResponse)
async def quark_upload(background_tasks: BackgroundTasks, response: Response,
                       files: list[UploadFile] = File(...),
                       relative_paths: str = Form(default="[]"),
                       parent_fid: str | None = Form(default=None),
                       record_id: str | None = Form(default=None),
                       user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    directory = None
    try:
        from app.services.quark_drive import _require_account, _validate_fid
        _require_account(int(user.id))
        if not 1 <= len(files) <= 1000:
            raise QuarkAuthError("每次请选择 1 到 1000 个文件")
        if parent_fid is not None:
            _validate_fid(parent_fid)
        if record_id:
            build_arguments("upload-resume", OperationRequest(record_id=record_id, confirmed=True))
            if len(files) != 1:
                raise QuarkAuthError("恢复上传时请重新选择对应的单个源文件")
        names = json.loads(relative_paths)
        if not isinstance(names, list) or names and len(names) != len(files):
            raise QuarkAuthError("上传路径与文件数量不匹配")
        paths = [safe_upload_name(name) for name in (names or [file.filename or "" for file in files]) if isinstance(name, str)]
        if len(paths) != len(files) or len(set(paths)) != len(paths):
            raise QuarkAuthError("上传列表中存在重复或无效路径")
        job_id = uuid4().hex
        directory = job_directory(int(user.id), job_id) / "uploads"
        directory.mkdir(mode=0o700)
        total = 0
        max_bytes = int(os.environ.get("QUARK_MAX_UPLOAD_BYTES", str(1024**3)))
        for file, relative in zip(files, paths):
            target = directory.joinpath(*relative.parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with target.open("xb") as stream:
                target.chmod(0o600)
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise QuarkAuthError(f"本次上传超过服务器限制（{max_bytes // 1024**2} MB）")
                    stream.write(chunk)
        sources = list(dict.fromkeys(str(directory / path.parts[0]) for path in paths))
        job = await create_job(int(user.id), "upload", {"source_paths": sources, "parent_fid": parent_fid,
                                "record_id": record_id, "title": f"上传 {len(files)} 个文件"}, job_id=job_id)
        background_tasks.add_task(run_job, int(user.id), job_id)
        return ApiResponse(success=True, data={"job": job})
    except (QuarkAuthError, ValueError, OSError) as exc:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        return ApiResponse(success=False, message=str(exc) if isinstance(exc, QuarkAuthError) else "接收上传文件失败，请检查文件名和本地空间")
    finally:
        for file in files:
            await file.close()


@router.post("/rename/plans", response_model=ApiResponse)
async def quark_rename_plan(payload: RenamePlanRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        return ApiResponse(success=True, data={"job": await prepare_rename(int(user.id), payload)})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/batch-organize/plans", response_model=ApiResponse)
async def quark_batch_plan(payload: BatchPlanRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        return ApiResponse(success=True, data={"job": await prepare_batch_organize(int(user.id), payload.proposal, payload.target_label)})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/jobs", response_model=ApiResponse)
async def quark_jobs(response: Response, limit: int = Query(default=50, ge=1, le=100),
                     offset: int = Query(default=0, ge=0), user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(success=True, data=await list_jobs(int(user.id), limit, offset))


@router.get("/jobs/{job_id}", response_model=ApiResponse)
async def quark_job(job_id: str, response: Response, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ApiResponse(success=True, data={"job": job_view(await get_job(int(user.id), job_id))})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/jobs/{job_id}/execute", response_model=ApiResponse)
async def quark_execute_plan(job_id: str, payload: ConfirmedRequest, background_tasks: BackgroundTasks,
                             user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        job = await queue_plan(int(user.id), job_id)
        background_tasks.add_task(run_job, int(user.id), job_id)
        return ApiResponse(success=True, data={"job": job})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/jobs/{job_id}/followup", response_model=ApiResponse)
async def quark_followup(job_id: str, payload: FollowupRequest, background_tasks: BackgroundTasks,
                        user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        operation = {"revert": "rename-revert", "rename-resume": "rename-resume", "resume": "batch-organize-resume",
                     "undo": "batch-organize-undo", "undo-resume": "batch-organize-undo"}[payload.action]
        job = await create_followup(int(user.id), job_id, operation, resume=payload.action == "undo-resume")
        background_tasks.add_task(run_job, int(user.id), job["id"])
        return ApiResponse(success=True, data={"job": job})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.post("/jobs/{job_id}/cancel", response_model=ApiResponse)
async def quark_cancel(job_id: str, payload: ConfirmedRequest, user: User = Depends(deps.get_current_active_user)) -> ApiResponse:
    try:
        return ApiResponse(success=True, data=await cancel_job(int(user.id), job_id))
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/jobs/{job_id}/files/{file_id}")
async def quark_download_result(job_id: str, file_id: str, user: User = Depends(deps.get_current_active_user)):
    try:
        path = await download_file_path(int(user.id), job_id, file_id)
        return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except QuarkAuthError as exc:
        return ApiResponse(success=False, message=str(exc))
