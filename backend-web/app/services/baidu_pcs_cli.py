"""Private stdin-only process boundary for the pinned BaiduPCS-Go bridge."""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from app.services.baidu_drive_auth import (
    BaiduDriveError, BaiduTokenExpired, PROJECT_ROOT, _locks, read_private, user_dir,
)
from app.services.baidu_drive_cli import private_directory, spawn, terminate, trusted_url

ROOT = "/"
MAX_OUTPUT = 16 * 1024**2
MAX_INPUT = 1024**2


@asynccontextmanager
async def read_lock(user_id: int):
    """Queue concurrent page reads while preserving the account mutation lock."""
    user_dir(user_id)
    lock = _locks.setdefault(user_id, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=35)
    except TimeoutError as exc:
        raise BaiduDriveError("当前百度网盘任务仍在执行，请完成后再读取") from exc
    try:
        yield
    finally:
        lock.release()


def binary() -> Path:
    path = Path(os.environ.get("BAIDU_PCS_BIN") or str(
        PROJECT_ROOT / "data/local-dev/runtime/baidupcs-go/xianyu-bridge")).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BaiduDriveError("BaiduPCS-Go 桥接程序尚未构建，请先完成本地安装")
    return path


def config_path(user_id: int) -> Path:
    root = user_dir(user_id) / "pcs"
    record = read_private(root / "active.json")
    if record is None:
        return root / "credentials.json"
    account_id = record.get("account_id", "") if isinstance(record, dict) else ""
    if not isinstance(account_id, str) or not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise BaiduDriveError("百度账号连接记录无效，请重新扫码登录")
    return root / "connections" / account_id / "credentials.json"


def _credentials(value: object) -> dict:
    if not isinstance(value, dict):
        raise BaiduTokenExpired("请先扫码登录百度网盘")
    result = {key: value.get(key, "") for key in ("bduss", "stoken", "cookies", "uid")}
    for key in ("bduss", "stoken"):
        if not isinstance(result[key], str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{6,4096}", result[key]):
            raise BaiduTokenExpired("百度网盘登录凭证缺失或无效，请重新扫码登录")
    if not isinstance(result["cookies"], str) or len(result["cookies"]) > 65536 or any(
        ord(c) < 32 or ord(c) > 126 for c in result["cookies"]
    ):
        raise BaiduTokenExpired("百度网盘登录凭证格式无效，请重新扫码登录")
    result["uid"] = str(result["uid"])
    if result["uid"] and not re.fullmatch(r"[0-9]{1,24}", result["uid"]):
        raise BaiduTokenExpired("百度网盘账号标识无效，请重新扫码登录")
    return result


def _parse(stdout: bytes, returncode: int) -> object:
    # Never forward provider text, stderr or a raw exception to API consumers.
    try:
        value = json.loads(stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BaiduDriveError("BaiduPCS-Go 未返回有效 JSON，请检查桥接程序版本") from exc
    if not isinstance(value, dict) or not isinstance(value.get("success"), bool):
        raise BaiduDriveError("BaiduPCS-Go 返回的数据格式不正确")
    if value["success"] is not True:
        code, kind = str(value.get("error_code", "")), value.get("kind")
        if kind == "auth" or str(value.get("provider_code", "")) in {"-6", "110", "111"} or code.lower() in {"auth_required", "auth_expired", "unauthorized", "token_expired", "not_logged_in"}:
            raise BaiduTokenExpired("百度网盘登录已失效，请重新扫码登录")
        messages = {
            "invalid_request": "百度网盘请求参数无效，请检查路径和文件信息",
            "invalid_path": "百度网盘路径无效，请刷新文件列表后重试",
            "not_found": "百度网盘文件不存在，请刷新文件列表",
            "network_error": "无法连接百度网盘，请检查网络后重试；已提交的操作请先刷新核验",
            "timeout": "百度网盘请求超时；已提交的操作请先刷新核验",
            "unsupported_operation": "当前百度网盘桥接版本不支持此操作",
        }
        if kind == "network":
            code = "network_error"
        elif kind == "input" and code not in messages:
            code = "invalid_request"
        raise BaiduDriveError(messages.get(code.lower(), "百度网盘操作未成功，请检查登录、路径和服务权限；已提交的操作请先刷新核验"))
    if returncode or "data" not in value:
        raise BaiduDriveError("BaiduPCS-Go 执行异常，未确认操作成功；请刷新核验")
    return value["data"]


async def run(user_id: int, operation: str, args: dict | None = None, *,
              credentials: dict | None = None, timeout: float = 60,
              disk_directory: Path | None = None, disk_limit: int = 256 * 1024**2) -> object:
    config = config_path(user_id)
    credential = _credentials(credentials if credentials is not None else read_private(config))
    if not isinstance(operation, str) or not re.fullmatch(r"[a-z_-]{1,40}", operation) or (args is not None and not isinstance(args, dict)):
        raise BaiduDriveError("百度网盘请求参数无效")
    try:
        payload = json.dumps({"operation": operation, "credentials": credential, "args": args or {}},
                             ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise BaiduDriveError("百度网盘请求参数格式无效") from exc
    if len(payload) > MAX_INPUT:
        raise BaiduDriveError("百度网盘请求数据过大")
    executable = binary()
    private_directory(config.parent)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("BAIDU_", "BAIDUPCS_", "BDPAN_")) and key.upper() not in {"BDUSS", "STOKEN", "BDSTOKEN"}}
    environment.update({"BAIDUPCS_GO_VERBOSE": "0", "TMPDIR": str(config.parent)})
    try:
        process = await spawn(str(executable), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=str(config.parent), env=environment, start_new_session=True)
    except OSError as exc:
        raise BaiduDriveError("无法启动百度网盘桥接程序，请检查本地安装") from exc

    async def collect():
        assert process.stdout is not None and process.stdin is not None
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        chunks, size = [], 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            if size > MAX_OUTPUT:
                raise BaiduDriveError("百度返回的数据过大，请缩小查询范围")
            chunks.append(chunk)
        return b"".join(chunks), await process.wait()

    async def monitor():
        while True:
            if disk_directory is not None:
                size = sum(p.stat().st_size for p in disk_directory.rglob("*") if p.is_file())
                if size > disk_limit:
                    raise BaiduDriveError("下载超出项目文件大小限制，请直接在百度网盘下载")
            await asyncio.sleep(0.25)

    collector, watcher = asyncio.create_task(collect()), asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait({collector, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise BaiduDriveError("百度网盘请求超时；已提交的修改可能仍在处理，请刷新核验后再操作")
        if watcher in done:
            await watcher
        output, returncode = await collector
        if disk_directory is not None:
            size = sum(p.stat().st_size for p in disk_directory.rglob("*") if p.is_file())
            if size > disk_limit:
                raise BaiduDriveError("下载超出项目文件大小限制，请直接在百度网盘下载")
        return _parse(output, returncode)
    except (BrokenPipeError, ConnectionError, OSError) as exc:
        raise BaiduDriveError("百度网盘桥接执行失败，请检查本地服务；已提交的操作请先刷新核验") from exc
    finally:
        await terminate(process)
        for task in (collector, watcher):
            task.cancel()
        await asyncio.gather(collector, watcher, return_exceptions=True)
