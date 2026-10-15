"""Freeze user-reviewed mappings before native rename/organize execution."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.quark_drive import _check_result, _require_account, _validate_fid, list_files
from app.services.quark_drive_auth import QuarkAuthError, _run_cli, _user_locks
from app.services.quark_drive_jobs import create_job, job_directory, private_path


class RenameItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fid: str = Field(min_length=1, max_length=2048)
    parent_fid: str = Field(min_length=1, max_length=2048)
    old_name: str = Field(min_length=1, max_length=255)
    new_name: str = Field(min_length=1, max_length=255)


class RenamePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[RenameItem] = Field(min_length=1, max_length=5000)


def fid_identity(fid: str) -> str:
    return fid.rsplit("|", 1)[-1] or fid


def validate_rename_items(items: list[RenameItem]) -> None:
    identities = set()
    names = set()
    for item in items:
        _validate_fid(item.fid); _validate_fid(item.parent_fid)
        identity = fid_identity(item.fid)
        if identity in identities:
            raise QuarkAuthError("重命名列表中有重复文件")
        identities.add(identity)
        if len(item.new_name.encode("utf-16-le", errors="surrogatepass")) // 2 > 255:
            raise QuarkAuthError("新文件名最多 255 个字符")
        key = (fid_identity(item.parent_fid), item.new_name)
        if key in names:
            raise QuarkAuthError("同一目录中不能给多个文件设置相同的新名称")
        names.add(key)


def _write_json(path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False)


async def prepare_rename(user_id: int, request: RenamePlanRequest) -> dict:
    validate_rename_items(request.items)
    by_parent: dict[str, dict] = {}
    for parent in dict.fromkeys(item.parent_fid for item in request.items):
        listing = await list_files(user_id, parent)
        by_parent[parent] = {fid_identity(row["fid"]): row for row in listing["files"]}
    frozen = []
    for item in request.items:
        row = by_parent[item.parent_fid].get(fid_identity(item.fid))
        if row is None or row.get("filename") != item.old_name:
            raise QuarkAuthError("选中文件或名称已发生变化，请刷新后重新生成方案")
        if item.old_name != item.new_name:
            frozen.append({"fid": row["fid"], "old_name": item.old_name, "new_name": item.new_name})
    if not frozen:
        raise QuarkAuthError("文件名没有变化，无需执行重命名")
    job_id = uuid4().hex
    path = job_directory(user_id, job_id) / "rename-items.json"
    _write_json(path, {"schema_version": 1, "items": frozen})
    return await create_job(user_id, "rename", {"batch_id": uuid4().hex[:16], "items_file": str(path),
                            "preview": {"items": frozen, "total": len(frozen)}, "title": "批量重命名"},
                            status="planned", job_id=job_id)


async def prepare_batch_organize(user_id: int, proposal: dict, target_label: str | None = None) -> dict:
    if proposal.get("version") != 4 or not isinstance(proposal.get("new_folders"), list) or not isinstance(proposal.get("operations"), list):
        raise QuarkAuthError("整理方案必须使用 version=4，并包含 new_folders 与 operations")
    if len(json.dumps(proposal)) > 8 * 1024 * 1024:
        raise QuarkAuthError("整理方案过大，请按官方上限拆分")
    _require_account(user_id)
    job_id = uuid4().hex
    base = job_directory(user_id, job_id)
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise QuarkAuthError("当前夸克任务正在执行，请完成后再准备整理方案")
    async with lock:
        result = await _run_cli(user_id, "file-organize", "init", "--base-dir", str(base))
        data = _check_result(result, "初始化整理方案失败")
        task_dir = private_path(user_id, data.get("task_dir") or "")
        path = task_dir / "proposal.json"
        _write_json(path, proposal)
        checked = await _run_cli(user_id, "file-organize", "check", "--task-dir", str(task_dir),
                                  "--proposal-file", str(path), timeout=75)
        data = checked.get("data") or {}
        results = data.get("results") or []
        valid = next((row for row in results if row.get("valid")), None)
        if checked.get("code") != 0 or not valid:
            errors = [str(error.get("msg") or error.get("code") or "方案无效")
                      for row in results for error in row.get("errors", [])]
            raise QuarkAuthError("整理方案校验失败：" + ("；".join(errors[:5]) or str(checked.get("msg") or "请检查范围与名称")))
        preview = valid.get("preview") or {}
        digest = preview.get("plan_digest")
        if not isinstance(digest, str) or len(digest) != 64:
            raise QuarkAuthError("夸克未返回可确认的整理方案")
    return await create_job(user_id, "batch-organize", {"task_dir": str(task_dir), "proposal_file": str(path),
                            "digest": digest, "preview": {"summary": preview, "proposal": proposal, "target_label": target_label}, "title": "批量整理"},
                            status="planned", job_id=job_id)
