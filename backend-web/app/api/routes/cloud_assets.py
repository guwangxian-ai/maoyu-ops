"""User-owned unified assets, asynchronous sync/share and card publishing APIs."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.api import deps
from app.services import cloud_assets as assets, cloud_asset_providers as providers
from app.services.cloud_asset_storage import AssetError
from common.models.user import User
from common.schemas.common import ApiResponse


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/cloud-drive/assets", tags=["多网盘同步资料"], dependencies=[Depends(private_response)])


class Confirmed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class ShareRequest(Confirmed):
    period: Literal[0, 1, 7, 30] = 7


class CardRequest(Confirmed):
    item_ids: list[str] = Field(default_factory=list, max_length=100)


class RenameRequest(Confirmed):
    name: str = Field(min_length=1, max_length=100)


class ImportRequest(RenameRequest):
    provider: Literal["quark", "baidu"]
    providers: list[Literal["quark", "baidu"]] | None = Field(default=None, max_length=2)
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    paths: list[str] = Field(default_factory=list, max_length=100)


class URLRequest(RenameRequest):
    providers: list[Literal["quark", "baidu"]] = Field(min_length=1, max_length=2)
    url: str = Field(min_length=1, max_length=4096)
    filename: str = Field(min_length=1, max_length=200)


class ShareImportRequest(RenameRequest):
    providers: list[Literal["quark", "baidu"]] = Field(min_length=1, max_length=2)
    provider: Literal["quark", "baidu"]
    url: str = Field(min_length=1, max_length=4096)
    password: str = Field(default="", pattern=r"^[A-Za-z0-9]{0,16}$", repr=False)


async def reply(awaitable):
    try:
        return ApiResponse(success=True, data=await awaitable)
    except (AssetError, providers.qa.QuarkAuthError, providers.ba.BaiduDriveError) as exc:
        return ApiResponse(success=False, message=str(exc))


@router.get("/status", response_model=ApiResponse)
async def status(user: User = Depends(deps.get_current_active_user)):
    return await reply(providers.status(int(user.id)))


@router.get("", response_model=ApiResponse)
@router.get("/", response_model=ApiResponse, include_in_schema=False)
async def listing(page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=20, ge=1, le=100), user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.listing(int(user.id), page, page_size))


@router.post("/upload", response_model=ApiResponse)
async def upload(name: str = Form(...), providers: str = Form(...), confirmed: bool = Form(False),
                 files: list[UploadFile] = File(...), user: User = Depends(deps.get_current_active_user)):
    try:
        if not confirmed:
            raise AssetError("上传资料需明确确认")
        try:
            selected = json.loads(providers)
        except ValueError as exc:
            raise AssetError("网盘列表格式无效") from exc
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            raise AssetError("网盘列表格式无效")
        return await reply(assets.upload(int(user.id), name, selected, files))
    except AssetError as exc:
        return ApiResponse(success=False, message=str(exc))
    finally:
        for file in files:
            await file.close()


@router.post("/import", response_model=ApiResponse)
async def import_file(payload: ImportRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.import_source(int(user.id), payload.name, payload.providers,
        {"provider": payload.provider, "file_ids": payload.file_ids, "paths": payload.paths}))


@router.post("/import-url", response_model=ApiResponse)
async def import_url(payload: URLRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.import_source(int(user.id), payload.name, payload.providers,
        {"url": payload.url, "filename": payload.filename}))


@router.post("/import-share", response_model=ApiResponse)
async def import_share(payload: ShareImportRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.import_source(int(user.id), payload.name, payload.providers,
        {"provider": payload.provider, "url": payload.url, "password": payload.password}))


@router.get("/{identifier}", response_model=ApiResponse)
async def get(identifier: str, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.get(int(user.id), identifier))


@router.patch("/{identifier}", response_model=ApiResponse)
async def rename(identifier: str, payload: RenameRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.rename(int(user.id), identifier, payload.name))


@router.post("/{identifier}/versions", response_model=ApiResponse)
async def version(identifier: str, confirmed: bool = Form(False), files: list[UploadFile] = File(...), user: User = Depends(deps.get_current_active_user)):
    try:
        if not confirmed:
            return ApiResponse(success=False, message="更新资料版本需明确确认")
        return await reply(assets.replace_version(int(user.id), identifier, files))
    finally:
        for file in files:
            await file.close()


@router.post("/{identifier}/sync", response_model=ApiResponse)
async def sync(identifier: str, payload: Confirmed, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.enqueue(int(user.id), identifier, "sync"))


@router.post("/{identifier}/shares", response_model=ApiResponse)
async def shares(identifier: str, payload: ShareRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.enqueue(int(user.id), identifier, "shares", {"period": payload.period}))


@router.post("/{identifier}/retry", response_model=ApiResponse)
async def retry(identifier: str, payload: Confirmed, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.retry(int(user.id), identifier))


@router.post("/{identifier}/analyze", response_model=ApiResponse)
async def analyze(identifier: str, payload: Confirmed, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.analyze(int(user.id), identifier))


@router.post("/{identifier}/card", response_model=ApiResponse)
async def card(identifier: str, payload: CardRequest, user: User = Depends(deps.get_current_active_user)):
    return await reply(assets.create_card(int(user.id), identifier, payload.item_ids))
