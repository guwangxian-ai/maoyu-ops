"""Thin, private-config adapter for the official bdpan CLI (Skill v1.7.5)."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from urllib.parse import urlsplit

from app.services.baidu_drive_auth import BaiduDriveError, PROJECT_ROOT, read_private, user_dir

VENDOR = PROJECT_ROOT / "backend-web/vendor/bdpan-storage"
ROOT = "/apps/bdpan"


def binary() -> Path:
    candidate = Path(os.environ.get("BAIDU_BDPAN_BIN") or str(PROJECT_ROOT / "data/local-dev/runtime/bdpan/bdpan")).expanduser().resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise BaiduDriveError("官方 bdpan CLI 尚未安装，请运行项目 scripts/install_bdpan.py 或配置 BAIDU_BDPAN_BIN")
    return candidate


def config_path(user_id: int) -> Path:
    root = user_dir(user_id) / "official"
    record = read_private(root / "active.json", {})
    connection = record.get("account_id", "") if isinstance(record, dict) else ""
    if connection:
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", connection):
            raise BaiduDriveError("百度账号连接记录无效，请重新授权")
        return root / "connections" / connection / "config.json"
    return root / "config.json"


def private_directory(directory: Path) -> None:
    missing, parent = [], directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)


def child_env(config: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in ("BDPAN_CONFIG_PATH", "BDPAN_CONFIG_DIR", "BDPAN_INSTALL_DIR"):
        environment.pop(key, None)
    environment.update({"PROJECT_BDPAN_BIN": str(binary()), "PROJECT_BDPAN_CONFIG": str(config),
                        "PATH": str(VENDOR) + os.pathsep + os.defpath, "TMPDIR": str(config.parent)})
    return environment


def trusted_url(value: object, *, authorization: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 for c in value):
        raise BaiduDriveError("百度未返回有效链接")
    try:
        url = urlsplit(value)
        valid = url.scheme == "https" and url.port in {None, 443} and not url.username and not url.password
        host = url.hostname or ""
        valid = valid and (host == "baidu.com" or host.endswith(".baidu.com"))
        if authorization:
            valid = valid and host in {"openapi.baidu.com", "pan.baidu.com", "passport.baidu.com"}
    except ValueError:
        valid = False
    if not valid:
        raise BaiduDriveError("百度返回的链接不属于可信官方 HTTPS 地址")
    return value


async def terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def spawn(*argv: str, **options) -> asyncio.subprocess.Process:
    """Keep private file creation compatible with asyncio and Uvicorn's uvloop.

    uvloop rejects create_subprocess_exec(umask=...). Set the mask in a tiny
    child shell, then replace it with the real process. Positional arguments
    stay separate, including filenames, URLs, spaces and metacharacters.
    The web server's process-wide umask is never changed.
    """
    return await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", 'umask 077; exec "$@"', "bdpan-private", *argv, **options)


def _parse(stdout: bytes, returncode: int) -> object:
    # Never expose raw stdout/stderr; provider errors can contain tokens or codes.
    if returncode:
        raise BaiduDriveError(f"官方 bdpan 执行失败（退出码 {returncode}）；请检查授权、路径及百度服务权限")
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeError) as exc:
        raise BaiduDriveError("官方 bdpan 未返回有效 JSON，请检查 CLI 版本") from exc
    if isinstance(result, dict):
        code = result.get("code", result.get("errno", result.get("error_code", 0)))
        if str(code) not in {"0", "None"} or result.get("error") or result.get("success") is False or result.get("status") in {"failed", "error", "cancelled"}:
            raise BaiduDriveError("百度网盘操作失败，请检查路径、授权和百度服务权限；已提交的操作请先刷新核验")
        if "code" in result and isinstance(result.get("data"), (dict, list)):
            result = result["data"]
    return result


async def run(user_id: int, *args: str, config: Path | None = None, timeout: float = 60,
              disk_directory: Path | None = None, disk_limit: int = 256 * 1024**2) -> object:
    configuration = config or config_path(user_id)
    private_directory(configuration.parent)
    process = await spawn(str(binary()), "--config-path", str(configuration),
        "--no-check-update", "--json", *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=child_env(configuration), start_new_session=True)
    async def collect():
        assert process.stdout is not None
        chunks, size = [], 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            if size > 16 * 1024**2:
                raise BaiduDriveError("百度返回的数据过大，请缩小查询范围")
            chunks.append(chunk)
        return b"".join(chunks), await process.wait()
    async def monitor():
        while process.returncode is None:
            if disk_directory is not None:
                size = sum(p.stat().st_size for p in disk_directory.rglob("*") if p.is_file())
                if size > disk_limit:
                    raise BaiduDriveError("下载超出项目文件大小限制，请直接在百度网盘下载")
            await asyncio.sleep(0.5)
    collector, watcher = asyncio.create_task(collect()), asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait({collector, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise BaiduDriveError("官方 bdpan 请求超时；已提交的修改可能仍在处理，请刷新核验后再操作")
        if watcher in done:
            await watcher
        output, returncode = await collector
        return _parse(output, returncode)
    finally:
        await terminate(process)
        for task in (collector, watcher):
            task.cancel()
        await asyncio.gather(collector, watcher, return_exceptions=True)
        if configuration.exists():
            configuration.chmod(0o600)
