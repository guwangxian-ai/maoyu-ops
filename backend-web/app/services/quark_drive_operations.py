"""Allowlisted business operations; never accepts CLI commands or host paths."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.services.quark_drive import _check_result, _read_file_artifact, _require_account, _validate_fid
from app.services.quark_drive_auth import QuarkAuthError, _run_cli, _read_lock

Operation = Literal[
    "create-folder", "move", "download", "summary", "qa", "organize", "organize-copy", "organize-move",
    "share-detail", "share-search", "get-share-update-list", "get-share-update-files", "get-share-saved-dir",
    "saveas", "saveas-update", "upload-list", "upload-resume", "upload-delete",
    "download-list", "download-resume", "download-delete", "search", "browse", "get-user-info",
]
READ_OPERATIONS = {"share-detail", "share-search", "get-share-update-list", "get-share-update-files",
                   "get-share-saved-dir", "upload-list", "download-list", "search", "browse", "get-user-info"}
AI_OPERATIONS = {"summary", "qa"}
TRANSFER_STATES = {"pending", "hashing", "uploading", "paused", "success", "failed", "cancelled",
                   "post_hashing", "completed", "downloading"}
SEARCH_TYPES = Literal["mix", "video", "album", "doc", "audio", "dir", "package", "other", "app"]


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fids: list[str] = Field(default_factory=list, max_length=100)
    parent_fid: str | None = Field(default=None, max_length=2048)
    target_fid: str | None = Field(default=None, max_length=2048)
    name: str | None = Field(default=None, max_length=255)
    query: str | None = Field(default=None, max_length=10000)
    keyword: str | None = Field(default=None, max_length=50)
    url: str | None = Field(default=None, max_length=4096)
    passcode: str | None = Field(default=None, max_length=32)
    page: int = Field(default=1, ge=1, le=100000)
    size: int = Field(default=50, ge=1, le=100)
    search_type: SEARCH_TYPES = "mix"
    record_id: str | None = Field(default=None, max_length=200)
    task_id: str | None = Field(default=None, max_length=200)
    state: str | None = Field(default=None, max_length=30)
    use_saved_directory: bool = False
    confirmed: bool = False


def validate_share_url(value: str | None) -> str:
    try:
        parsed = urlsplit(value or "")
        port = parsed.port
    except ValueError as exc:
        raise QuarkAuthError("夸克分享链接格式无效") from exc
    if (parsed.scheme != "https" or parsed.hostname != "pan.quark.cn" or port not in (None, 443)
            or parsed.username or parsed.password or not re.fullmatch(r"/s/[A-Za-z0-9_-]+/?", parsed.path)):
        raise QuarkAuthError("请输入完整的夸克分享链接（https://pan.quark.cn/s/…）")
    return value or ""


def _text(value: str | None, label: str) -> str:
    if not value or not value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise QuarkAuthError(f"请填写{label}")
    return value.strip()


def _identifier(value: str | None, label: str) -> str:
    if not value or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,200}", value) or value.startswith("-"):
        raise QuarkAuthError(f"{label}无效，请刷新任务列表")
    return value


def build_arguments(operation: str, params: OperationRequest) -> tuple[str, list[str]]:
    if operation not in READ_OPERATIONS and not params.confirmed:
        raise QuarkAuthError("请确认本次操作后提交（confirmed=true）")
    for fid in params.fids:
        _validate_fid(fid)
    if len(set(params.fids)) != len(params.fids):
        raise QuarkAuthError("文件列表包含重复项")
    for fid in (params.parent_fid, params.target_fid):
        if fid is not None:
            _validate_fid(fid)
    args: list[str] = []
    command = operation
    if operation in {"move", "download", "summary", "qa"} and not params.fids:
        raise QuarkAuthError("请先选择文件或文件夹")
    if operation == "create-folder":
        args = ["--dir-path", _text(params.name, "文件夹名称")]
        if params.parent_fid is not None:
            args += ["--parent-fid", params.parent_fid]
    elif operation == "move":
        if params.target_fid is None:
            raise QuarkAuthError("请选择目标文件夹")
        args = [*params.fids, "--target-fid", params.target_fid]
    elif operation == "download":
        # The worker expands a batch into the CLI's single-file command.
        args = ["--fid", params.fids[0]]
    elif operation in AI_OPERATIONS:
        args = ["--query", _text(params.query, "问题或总结要求"), "--fid-list", ",".join(params.fids)]
    elif operation == "organize":
        args = ["--query", _text(params.query, "照片或视频的范围与整理方式")]
    elif operation in {"organize-copy", "organize-move"}:
        args = ["--task-id", _identifier(params.task_id, "整理任务")]
    elif operation in {"share-detail", "share-search", "get-share-update-files", "get-share-saved-dir", "saveas", "saveas-update"}:
        args = ["--url", validate_share_url(params.url)]
        if operation in {"share-detail", "share-search"}:
            args += ["--page", str(params.page), "--size", str(params.size)]
            if params.passcode:
                # share-search reads passcode from the URL; detail also has an explicit option.
                if operation == "share-detail":
                    args += ["--passcode", params.passcode]
                else:
                    from urllib.parse import parse_qsl, urlencode, urlunsplit
                    url = urlsplit(args[1]); query = dict(parse_qsl(url.query)); query["pwd"] = params.passcode
                    args[1] = urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment))
        if operation == "share-detail" and params.parent_fid is not None:
            args += ["--pdir-fid", params.parent_fid]
        if operation == "share-search":
            args += ["--keyword", _text(params.keyword, "搜索关键词")]
        if operation == "get-share-update-files":
            args += ["--page", str(params.page)]
        if operation == "saveas":
            if params.fids:
                args += ["--fid-list", ",".join(params.fids)]
            if params.target_fid is not None and not params.use_saved_directory:
                args += ["--to-pdir-fid", params.target_fid]
            if params.passcode:
                args += ["--passcode", params.passcode]
        if operation == "saveas-update" and (params.fids or params.target_fid is not None):
            raise QuarkAuthError("转存全部更新会使用上次转存目录，请勿指定文件或其他目录")
    elif operation == "get-share-update-list":
        if params.size > 50:
            raise QuarkAuthError("分享更新列表每页最多 50 条")
        args = ["--page", str(params.page), "--size", str(params.size)]
    elif operation.startswith("upload-") or operation.startswith("download-"):
        command, subcommand = operation.split("-", 1)
        args = [subcommand]
        if subcommand == "list":
            if params.state:
                if params.state not in TRANSFER_STATES:
                    raise QuarkAuthError("传输状态无效")
                args += ["--state", params.state]
        else:
            args += ["--record-id", _identifier(params.record_id, "传输任务")]
    elif operation == "browse":
        args = ["--parent-fid", params.parent_fid or "0", "--page-size", str(params.size), "--all"]
    elif operation == "search":
        args = ["--keyword", _text(params.keyword, "搜索关键词"), "--size", str(params.size),
                "--search-type", params.search_type]
        if params.parent_fid not in (None, "0"):
            args += ["--parent-fid", params.parent_fid]
    elif operation != "get-user-info":
        raise QuarkAuthError("此操作未开放")
    return command, args


async def execute_query(user_id: int, operation: str, params: OperationRequest) -> dict:
    command, args = build_arguments(operation, params)
    if operation not in READ_OPERATIONS:
        raise QuarkAuthError("此操作需要创建后台任务")
    async with _read_lock(user_id):
        _require_account(user_id)
        result = await _run_cli(user_id, command, *args, timeout=75)
        data = _check_result(result, "夸克查询失败")
        if operation in {"search", "browse"}:
            files = _read_file_artifact(user_id, result.get("_artifact"))
            return {"message": result.get("msg"), "files": files, "total": len(files)}
        return {"message": result.get("msg"), "data": data, "items": result.get("_items") or []}
