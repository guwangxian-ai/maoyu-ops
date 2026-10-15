"""Background jobs following the application's existing MySQL/task pattern."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from common.db.session import async_session_maker
from common.models.cloud_drive_job import CloudDriveJob
from app.services.quark_drive import _require_account, _validate_fid
from app.services.quark_drive_auth import (
    QuarkAuthError, QuarkTimeoutError, _run_cli, _user_dir, _user_locks,
    _account_key, _legacy_account_key, _record_legacy_account,
)
from app.services.quark_drive_operations import OperationRequest, build_arguments

_running: dict[str, asyncio.Task] = {}
_job_gate = asyncio.Semaphore(4)
ACTIVE = ("queued", "running")
PRIVATE_FIELDS = {"accessToken", "refreshToken", "clientToken", "access_token", "refresh_token", "stoken",
                  "share_fid_token", "filePath", "file_path", "record_file", "task_dir", "proposal_file", "output_dir",
                  "local_path", "source_paths", "items_file", "config_file", "proposal_file_path", "clientSecret"}


def public_data(value):
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items()
                if key not in PRIVATE_FIELDS and not key.startswith("_")}
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return value


def private_path(user_id: int, value: str | Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(_user_dir(user_id).resolve()):
        raise QuarkAuthError("任务文件位置无效")
    return path


def job_directory(user_id: int, job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise QuarkAuthError("任务标识无效")
    path = _user_dir(user_id) / "jobs" / job_id
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def job_view(job: CloudDriveJob) -> dict:
    params = job.params or {}
    files = params.get("download_files") or []
    return {"id": job.id, "operation": job.operation, "status": job.status,
            "message": (job.message or "").replace(str(_user_dir(job.owner_id)), "[夸克运行目录]"),
            "title": str(params.get("name") or params.get("query") or params.get("title") or job.operation)[:100],
            "progress": public_data(job.progress), "result": public_data(job.result),
            "preview": public_data(params.get("preview")),
            "files": [{"id": row["id"], "name": row["name"], "size": row["size"]} for row in files],
            "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat()}


def _account_clause(user_id: int):
    key = _account_key(user_id)
    if not key:
        raise QuarkAuthError("未找到当前网盘账号，请重新授权")
    _record_legacy_account(user_id)
    column = CloudDriveJob.params["_account_id"].as_string()
    return or_(column == key, and_(column.is_(None), key == _legacy_account_key(user_id)))


def _check_job_account(user_id: int, params: dict) -> None:
    key = _account_key(user_id)
    assigned = params.get("_account_id") or _legacy_account_key(user_id)
    if not key or assigned != key:
        raise QuarkAuthError("此任务属于其他网盘账号，请切换回原账号后操作")


async def get_job(user_id: int, job_id: str) -> CloudDriveJob:
    async with async_session_maker() as session:
        job = await session.scalar(select(CloudDriveJob).where(
            CloudDriveJob.id == job_id, CloudDriveJob.owner_id == user_id, CloudDriveJob.provider == "quark"))
        if job is None:
            raise QuarkAuthError("任务不存在或无权访问")
        _check_job_account(user_id, job.params or {})
        return job


async def list_jobs(user_id: int, limit: int = 50, offset: int = 0) -> dict:
    async with async_session_maker() as session:
        rows = (await session.scalars(select(CloudDriveJob).where(
            CloudDriveJob.owner_id == user_id, CloudDriveJob.provider == "quark", _account_clause(user_id)
        ).order_by(CloudDriveJob.created_at.desc(), CloudDriveJob.id.desc()).offset(offset).limit(limit))).all()
    return {"jobs": [job_view(job) for job in rows], "has_more": len(rows) == limit}


async def _save(job_id: str, **values) -> None:
    async with async_session_maker() as session:
        await session.execute(update(CloudDriveJob).where(CloudDriveJob.id == job_id).values(**values))
        await session.commit()


async def create_job(user_id: int, operation: str, params: dict, *, status: str = "queued", job_id: str | None = None) -> dict:
    _require_account(user_id)
    key = _account_key(user_id)
    if not key:
        raise QuarkAuthError("无法确认当前网盘账号，请检查授权")
    _record_legacy_account(user_id)
    params = {**params, "_account_id": key}
    async with async_session_maker() as session:
        active = (await session.scalars(select(CloudDriveJob.id).where(
            CloudDriveJob.owner_id == user_id, CloudDriveJob.status.in_(ACTIVE)))).all()
        if len(active) >= 5:
            raise QuarkAuthError("当前已有 5 个任务等待或执行，请完成后再提交")
        job = CloudDriveJob(id=job_id or uuid4().hex, owner_id=user_id, operation=operation,
                            params=params, status=status, message="方案待确认" if status == "planned" else "等待执行")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job_view(job)


async def queue_plan(user_id: int, job_id: str) -> dict:
    # Atomic claim prevents a double-click from submitting a plan twice.
    async with async_session_maker() as session:
        result = await session.execute(update(CloudDriveJob).where(
            CloudDriveJob.id == job_id, CloudDriveJob.owner_id == user_id,
            CloudDriveJob.provider == "quark", CloudDriveJob.operation.in_(("rename", "batch-organize")),
            CloudDriveJob.status == "planned", _account_clause(user_id)).values(status="queued", message="方案已确认，等待执行"))
        if result.rowcount != 1:
            raise QuarkAuthError("此方案已提交或不可执行，请查看任务状态")
        await session.commit()
    return job_view(await get_job(user_id, job_id))


async def cancel_job(user_id: int, job_id: str) -> dict:
    job = await get_job(user_id, job_id)
    if job.status not in ACTIVE:
        raise QuarkAuthError("此任务已经结束")
    task = _running.get(job_id)
    if task is not None:
        task.cancel()
    else:
        await _save(job_id, status="interrupted", message="任务已停止；已提交的操作请在夸克核验")
    return {"id": job_id, "message": "已请求停止。传输断点可在传输记录中恢复；停止不会撤回已完成操作。"}


async def recover_interrupted_jobs() -> None:
    async with async_session_maker() as session:
        await session.execute(update(CloudDriveJob).where(CloudDriveJob.provider == "quark",
            CloudDriveJob.status.in_(ACTIVE)).values(status="interrupted",
            message="服务已重启，任务未自动重提。请核验结果或使用原任务的恢复/撤回功能。"))
        await session.commit()


async def stop_running_jobs() -> None:
    tasks = list(_running.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _download_manifest(user_id: int, job_id: str, output_path: Path, successful_paths: list[str]) -> list[dict]:
    output = private_path(user_id, output_path)
    return [{"id": uuid4().hex, "name": path.name, "size": path.stat().st_size, "path": str(path)}
            for path in sorted({Path(value) for value in successful_paths}) if path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(output.resolve())]


async def download_file_path(user_id: int, job_id: str, file_id: str) -> Path:
    job = await get_job(user_id, job_id)
    for row in (job.params or {}).get("download_files", []):
        if row.get("id") == file_id:
            path = private_path(user_id, row["path"])
            if path.is_file():
                return path
    raise QuarkAuthError("下载文件不存在或已清理")


async def original_download_output(user_id: int, record_id: str) -> Path:
    """A resumed file must reuse its original bytes, never a fresh output folder."""
    async with async_session_maker() as session:
        rows = (await session.scalars(select(CloudDriveJob).where(
            CloudDriveJob.owner_id == user_id, CloudDriveJob.provider == "quark",
            CloudDriveJob.operation.in_(("download", "download-resume")), _account_clause(user_id)
        ).order_by(CloudDriveJob.created_at.desc()))).all()
    for job in rows:
        params = job.params or {}
        if record_id in params.get("_download_records", []) and params.get("_download_output_dir"):
            path = private_path(user_id, params["_download_output_dir"])
            if path.is_dir():
                return path
    raise QuarkAuthError("没有找到此断点的原下载目录，请重新下载；不会在空目录中伪造恢复")


async def run_job(user_id: int, job_id: str) -> None:
    current = asyncio.current_task()
    assert current is not None
    _running[job_id] = current
    last_progress = 0.0
    events = []
    params: dict = {}
    try:
        job = await get_job(user_id, job_id)
        if job.status != "queued":
            return
        params = dict(job.params)
        async with _job_gate, _user_locks.setdefault(user_id, asyncio.Lock()):
            _require_account(user_id)
            _check_job_account(user_id, params)
            await _save(job_id, status="running", message="正在调用夸克接口")

            async def on_event(event: dict) -> None:
                nonlocal last_progress
                if event.get("type") == "list":
                    events.append(event)
                if event.get("type") in ("list", "progress"):
                    now = time.monotonic()
                    if now - last_progress >= 1 or event.get("type") == "list":
                        last_progress = now
                        await _save(job_id, progress={"data": public_data(event.get("data") or {}),
                            "message": str(event.get("msg") or "处理中"), "completed_items": len(events)})

            async def call(command: str, *args: str, timeout: float = 600) -> dict:
                return await _run_cli(user_id, command, *args, timeout=timeout, on_event=on_event)

            operation = job.operation
            if operation == "upload":
                paths = [str(private_path(user_id, path)) for path in params["source_paths"]]
                args = paths + (["--parent-fid", params["parent_fid"]] if params.get("parent_fid") is not None else [])
                if params.get("record_id"):
                    result = await call("upload", "resume", "--record-id", params["record_id"],
                                        "--file-path", paths[0], timeout=86400)
                else:
                    result = await call("upload", *args, timeout=86400)
            elif operation in {"rename", "rename-resume", "rename-revert"}:
                if operation in {"rename", "rename-resume"}:
                    result = await call("rename", "--batch-id", params["batch_id"],
                                        "--items-file", str(private_path(user_id, params["items_file"])))
                else:
                    source = await get_job(user_id, params["source_job_id"])
                    original = (source.result or {}).get("data") or {}
                    record = private_path(user_id, original.get("record_file") or "")
                    result = await call("rename-revert", "--batch-id", source.params["batch_id"], "--record-file", str(record))
            elif operation.startswith("batch-organize"):
                task_dir = str(private_path(user_id, params["task_dir"]))
                if operation == "batch-organize":
                    result = await call("file-organize", "apply", "--task-dir", task_dir,
                                        "--proposal-file", str(private_path(user_id, params["proposal_file"])),
                                        "--digest", params["digest"], timeout=86400)
                elif operation == "batch-organize-resume":
                    result = await call("file-organize", "apply", "--task-dir", task_dir, "--resume", timeout=86400)
                else:
                    args = ["undo", "--task-dir", task_dir]
                    if params.get("resume"):
                        args.append("--resume")
                    result = await call("file-organize", *args, timeout=86400)
            else:
                # Account binding and transfer manifests are server metadata,
                # never CLI inputs or fields of the public operation schema.
                request = OperationRequest.model_validate({key: value for key, value in params.items()
                    if not key.startswith("_") and key != "download_files"})
                command, args = build_arguments(operation, request)
                if operation in {"download", "download-resume"}:
                    output = await original_download_output(user_id, request.record_id) if operation == "download-resume" else job_directory(user_id, job_id) / "downloads"
                    output.mkdir(mode=0o700, exist_ok=True)
                    before = await _run_cli(user_id, "download", "list", timeout=30)
                    previous = {row.get("recordId") for row in before.get("_items", []) if isinstance(row, dict)}
                    params["_download_output_dir"] = str(output)
                    await _save(job_id, params=params)
                    try:
                        if operation == "download":
                            results = []
                            for fid in request.fids:
                                results.append(await call("download", "--fid", fid, "--output-dir", str(output), timeout=86400))
                            failed = sum(row.get("code") != 0 for row in results)
                            result = {"code": 0 if not failed else -1, "msg": f"下载完成：成功 {len(results)-failed} 项，失败 {failed} 项",
                                      "data": {"results": results, "success_count": len(results)-failed, "fail_count": failed}}
                        else:
                            result = await call(command, *args, "--output-dir", str(output), timeout=86400)
                        completed = result.get("data", {}).get("results", [result])
                        paths = [row.get("data", {}).get("filePath") for row in completed if row.get("code") == 0]
                        params["download_files"] = _download_manifest(user_id, job_id, output,
                            [value for value in paths if isinstance(value, str)])
                    finally:
                        # Still inside the account lock: no other job can create
                        # unrelated records between the before/after snapshots.
                        try:
                            after = await _run_cli(user_id, "download", "list", timeout=30)
                            ids = {row.get("recordId") for row in after.get("_items", []) if isinstance(row, dict)}
                            params["_download_records"] = sorted(rid for rid in ids if isinstance(rid, str)
                                and ((before.get("code") == 0 and rid not in previous) or rid == request.record_id))
                            await _save(job_id, params=params)
                        except QuarkAuthError:
                            pass  # Never guess a path if provider record lookup fails.
                else:
                    if operation == "saveas" and request.use_saved_directory and request.fids:
                        directory = await call("get-share-saved-dir", "--url", request.url or "", timeout=30)
                        fid = (directory.get("data") or {}).get("pdir_fid") if directory.get("code") == 0 else None
                        if isinstance(fid, str) and fid:
                            args += ["--to-pdir-fid", _validate_fid(fid)]
                    result = await call(command, *args, timeout=240 if operation in {"summary", "qa", "organize"} else 600)
            code = result.get("code")
            status = "succeeded" if code == 0 else "needs_confirmation" if code == -1609 else "failed"
            data = result.get("data") or {}
            if code == 0 and (data.get("fail_count", 0) or data.get("failCount", 0) or data.get("unknown_count", 0)):
                status = "partial"
            if code != 0 and (data.get("success_count", 0) or data.get("successCount", 0)
                              or any(event.get("code") == 0 for event in events)):
                status = "partial"
            if data.get("result_status") == "OUTCOME_UNKNOWN":
                status = "unknown"
            if code == 0 and operation in {"organize-copy", "organize-move"}:
                status = "submitted"
            result["items"] = events
            await _save(job_id, status=status, result=result, params=params,
                        message=str(result.get("msg") or "夸克没有返回结果说明"))
    except asyncio.CancelledError:
        await _save(job_id, status="interrupted", params=params or (await get_job(user_id, job_id)).params,
                    message="任务已停止等待，已完成或已提交的操作不会自动撤回；请核验或恢复原任务。")
    except Exception as exc:
        message = str(exc) if isinstance(exc, QuarkAuthError) else "任务执行异常，请检查服务日志和夸克中的实际结果"
        await _save(job_id, status="unknown" if isinstance(exc, QuarkTimeoutError) else "failed", message=message)
    finally:
        _running.pop(job_id, None)


async def create_followup(user_id: int, source_job_id: str, operation: str, *, resume: bool = False) -> dict:
    source = await get_job(user_id, source_job_id)
    if source.status in ACTIVE:
        raise QuarkAuthError("原任务仍在执行，请等待结束")
    if operation == "rename-resume":
        if source.operation not in {"rename", "rename-resume"} or not source.params.get("items_file"):
            raise QuarkAuthError("此任务没有可接续的重命名方案")
        if (source.result or {}).get("code") == 0 or source.status not in {"failed", "interrupted", "unknown"}:
            raise QuarkAuthError("此重命名批次已经返回终态，请核对原结果，不能重复提交")
        params = {"source_job_id": source.id, "batch_id": source.params["batch_id"], "items_file": source.params["items_file"],
                  "preview": source.params.get("preview")}
    elif operation == "rename-revert":
        if source.operation not in {"rename", "rename-resume"} or not (source.result or {}).get("data", {}).get("record_file"):
            raise QuarkAuthError("此任务没有可用的重命名撤回记录")
        params = {"source_job_id": source.id}
    else:
        if not source.operation.startswith("batch-organize") or not source.params.get("task_dir"):
            raise QuarkAuthError("此任务没有可用的批量整理记录")
        params = {"task_dir": source.params["task_dir"], "source_job_id": source.id, "resume": resume}
    return await create_job(user_id, operation, params)
