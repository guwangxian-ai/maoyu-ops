"""Thin, per-site-user adapter for the official Quark Drive Skill CLI.

The CLI owns the OAuth exchange and token rotation. Its on-disk state is kept
outside the web static directory and isolated by the authenticated site user.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


VENDOR_CLI = Path(__file__).resolve().parents[2] / "vendor/quarkclouddrive/quark-drive.cjs"
VENDOR_SHA256 = "0e132196bea28114cffd248b055f2e1bc64a521cb16c5cb5aba5e786199e572e"
WORKER_SHA256 = "c9e76fa0fd418e731980636c13bfe1379bdb60e117f1d6ed7dcf83ff2187a690"
AUTH_LINK_PATTERN = re.compile(r"https://pan\.quark\.cn/open/v1/oauth/agent\?[^\s<>\]\)\"']+")
AUTH_CODE_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{8,2048}$")
_user_locks: dict[int, asyncio.Lock] = {}
LOGIN_BRIDGE = Path(__file__).with_name("quark_login_bridge.cjs")
READ_BRIDGE = Path(__file__).with_name("quark_read_bridge.cjs")
FAST_READ_COMMANDS = frozenset({"get-user-info", "browse", "search"})


class QuarkAuthError(Exception):
    """A safe error message that may be displayed to the site user."""


class QuarkTimeoutError(QuarkAuthError):
    """The provider may still be processing the submitted operation."""


class QuarkBusyError(QuarkAuthError):
    """The bounded wait for another account operation has expired."""


@asynccontextmanager
async def _read_lock(user_id: int, timeout: float = 3):
    """Queue short reads without overlapping CLI processes or waiting on long jobs."""
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    try:
        async with asyncio.timeout(timeout):
            await lock.acquire()
    except TimeoutError as exc:
        raise QuarkBusyError("当前夸克任务正在执行，请在任务中心查看进度，完成后刷新文件") from exc
    try:
        yield
    finally:
        lock.release()


def _auth_root() -> Path:
    directory = os.environ.get("QUARK_AUTH_DIR")
    if directory is None:
        from app.core.config import get_settings

        directory = get_settings().quark_auth_dir
    path = Path(directory).expanduser()
    project_root = Path(__file__).resolve().parents[3]
    return (path if path.is_absolute() else project_root / path).resolve()


def _user_dir(user_id: int) -> Path:
    if user_id <= 0:
        raise QuarkAuthError("无效的用户账号")
    return _auth_root() / f"user-{user_id}"


def _has_account(user_id: int) -> bool:
    base = _user_dir(user_id)
    for path in (
        base / "skill/codex/config.json",
        base / "work/.quarkclouddrive/codex/config.json",
    ):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # `login` creates a device-only config before the user grants access.
        # The pinned runtime stores tokens under config[currentUserId]; some
        # documented versions instead use an `accounts` collection.
        if isinstance(config, dict):
            identity = config.get(str(config.get("currentUserId") or ""))
            if config.get("accounts") or (
                isinstance(identity, dict) and identity.get("refreshToken")
            ):
                return True
    return False


def _account_key(user_id: int) -> str | None:
    """A stable opaque account identifier; never return tokens or the config."""
    for path in (_user_dir(user_id) / "skill/codex/config.json",
                 _user_dir(user_id) / "work/.quarkclouddrive/codex/config.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            identity = config.get("currentUserId") if isinstance(config, dict) else None
            if isinstance(identity, (str, int)) and str(identity):
                return hashlib.sha256(str(identity).encode()).hexdigest()[:32]
        except (OSError, ValueError):
            continue
    return None


def _legacy_account_key(user_id: int) -> str | None:
    try:
        data = json.loads((_user_dir(user_id) / "account-binding.json").read_text(encoding="utf-8"))
        return data.get("legacy_account_id")
    except (OSError, ValueError):
        return None


def _record_legacy_account(user_id: int) -> None:
    """Bind pre-account-isolation records before a login can replace the account."""
    key = _account_key(user_id)
    path = _user_dir(user_id) / "account-binding.json"
    if not key or path.exists():
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f"account-binding.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            json.dump({"legacy_account_id": key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _prepare_user_runtime(user_id: int) -> tuple[Path, Path, Path, dict[str, str]]:
    if not VENDOR_CLI.is_file() or hashlib.sha256(VENDOR_CLI.read_bytes()).hexdigest() != VENDOR_SHA256:
        raise QuarkAuthError("夸克网盘组件缺失或校验失败，请检查部署包")
    node = shutil.which("node")
    if not node:
        raise QuarkAuthError("服务端缺少 Node.js，无法运行夸克网盘组件")

    root = _auth_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    base = _user_dir(user_id)
    _record_legacy_account(user_id)
    scripts = base / "skill/scripts"
    work = base / "work"
    private_bin = base / "bin"
    for directory in (base, scripts, work, private_bin, base / "home"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)

    # The official CLI resolves config.json relative to its script directory.
    # Copying the pinned bundle here gives each site user independent CLI state.
    cli = scripts / "quark-drive.cjs"
    if not cli.exists() or hashlib.sha256(cli.read_bytes()).hexdigest() != VENDOR_SHA256:
        temporary = scripts / f"quark-drive.{os.getpid()}.tmp"
        shutil.copyfile(VENDOR_CLI, temporary)
        temporary.chmod(0o600)
        temporary.replace(cli)

    worker_source = VENDOR_CLI.with_name("hash-worker.cjs")
    if not worker_source.is_file() or hashlib.sha256(worker_source.read_bytes()).hexdigest() != WORKER_SHA256:
        raise QuarkAuthError("夸克上传组件缺失或校验失败")
    worker = scripts / "hash-worker.cjs"
    if not worker.exists() or hashlib.sha256(worker.read_bytes()).hexdigest() != WORKER_SHA256:
        temporary = scripts / f"hash-worker.{os.getpid()}.tmp"
        shutil.copyfile(worker_source, temporary)
        temporary.chmod(0o600)
        temporary.replace(worker)

    # The CLI attempts to open a browser on the server. A PATH with only Node
    # makes it return the official authorization URL for the user's browser.
    private_node = private_bin / "node"
    if not private_node.exists():
        private_node.symlink_to(Path(node).resolve())
    env = {
        "PATH": str(private_bin),
        "HOME": str(base / "home"),
        "CODEX_ENV": "1",  # the official Skill supports the Codex agent channel
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        if name in os.environ:
            env[name] = os.environ[name]
    return private_node, cli, work, env


async def _run_cli(user_id: int, command: str, *args: str, timeout: float = 30,
                   on_event: Callable[[dict], Awaitable[None]] | None = None,
                   browser_bridge: bool = False,
                   fast_read: bool | None = None) -> dict:
    node, cli, work, env = _prepare_user_runtime(user_id)
    try:
        node_args = ["--require", str(LOGIN_BRIDGE)] if browser_bridge and command == "login" else []
        if command in FAST_READ_COMMANDS and READ_BRIDGE.is_file():
            if fast_read is None:
                from app.core.config import get_settings

                fast_read = get_settings().quark_fast_read
            if fast_read:
                node_args = ["--require", str(READ_BRIDGE)]
        process = await asyncio.create_subprocess_exec(
            str(node), *node_args, str(cli), command, *args,
            cwd=work, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=16 * 1024 * 1024,
        )
        async def collect_output() -> bytes:
            lines = []
            assert process.stdout is not None
            async for line in process.stdout:
                lines.append(line)
                if on_event is not None:
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(event, dict):
                        await on_event(event)
            await process.wait()
            return b"".join(lines)
        try:
            stdout = await asyncio.wait_for(collect_output(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise QuarkTimeoutError("夸克网盘请求超时，已提交的操作可能仍在执行，请先核验结果") from exc
        except BaseException:
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
    except OSError as exc:
        raise QuarkAuthError("夸克网盘组件启动失败") from exc

    for path in (_user_dir(user_id) / "skill/codex/config.json",
                 _user_dir(user_id) / "work/.quarkclouddrive/codex/config.json"):
        if path.exists():
            path.chmod(0o600)

    # Never log stdout/stderr: the CLI may include authorization codes or URLs.
    result = _parse_cli_output(stdout)
    if command in FAST_READ_COMMANDS and process.returncode != 0 and result.get("code") == 0:
        raise QuarkAuthError("夸克读取进程未正常完成，请重新读取文件或检查授权状态")
    return result


def _parse_cli_output(stdout: bytes) -> dict:
    final_result = None
    items = []
    artifact = None
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict):
            continue
        if result.get("type") == "list":
            items.append(result.get("data"))
        elif result.get("type") == "artifact":
            artifact = result.get("data")
        elif result.get("type") == "result":
            final_result = result
    if final_result is not None:
        final_result["_items"] = items
        final_result["_artifact"] = artifact
        return final_result
    raise QuarkAuthError("夸克网盘组件没有返回有效结果")


def _extract_auth_link(message: str) -> str | None:
    for match in AUTH_LINK_PATTERN.finditer(message):
        link = match.group(0)
        parsed = urlsplit(link)
        if (
            parsed.scheme == "https"
            and parsed.hostname == "pan.quark.cn"
            and parsed.path == "/open/v1/oauth/agent"
            and parse_qs(parsed.query).get("page_code")
        ):
            return link
    return None


async def get_status(user_id: int) -> dict:
    if not VENDOR_CLI.is_file() or not shutil.which("node"):
        return {"connected": False, "available": False, "message": "夸克授权组件尚未就绪"}
    try:
        async with _read_lock(user_id):
            if not _has_account(user_id):
                return {"connected": False, "available": True}
            result = await _run_cli(user_id, "get-user-info", timeout=25)
    except QuarkBusyError:
        return {"connected": _has_account(user_id), "available": True, "verified": False,
                "account_id": _account_key(user_id),
                "message": "当前夸克任务正在执行，可在任务中心查看进度"}
    except QuarkAuthError:
        return {"connected": _has_account(user_id), "available": True, "verified": False,
                "account_id": _account_key(user_id),
                "message": "授权已保存，暂时无法连接夸克，请稍后刷新"}
    account = {"account_id": _account_key(user_id)}
    if result.get("code") != 0:
        if result.get("code") in (-103, -1408):
            return {"connected": False, "available": True,
                    "message": "夸克授权已失效，请重新授权"}
        return {"connected": True, "available": True, "verified": False,
                **account, "message": "授权已保存，暂时无法验证夸克状态，请稍后刷新"}
    data = result.get("data") or {}
    user_info = data.get("userInfo") or {}
    vip_info = data.get("vipInfo") or {}
    return {
        "connected": True,
        "available": True,
        "verified": True,
        "account_id": _account_key(user_id),
        "nickname": str(user_info.get("nickname") or "夸克用户")[:100],
        "vip_type": str(vip_info.get("vipType") or "")[:40],
        "used_bytes": vip_info.get("used"),
        "capacity_bytes": vip_info.get("capacity"),
    }


async def start_authorization(user_id: int) -> dict:
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise QuarkAuthError("当前夸克任务正在执行，请完成后再授权")
    async with lock:
        result = await _run_cli(user_id, "login", timeout=30)
    link = _extract_auth_link(str(result.get("msg") or ""))
    if link:
        return {"authorization_url": link}
    if result.get("code") == -118:
        raise QuarkAuthError("夸克账号已授权，无需重复登录")
    raise QuarkAuthError("获取夸克授权链接失败，请稍后重试")


async def start_unauthorization(user_id: int) -> dict:
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise QuarkAuthError("请等待当前夸克任务完成后再解除授权")
    async with lock:
        result = await _run_cli(user_id, "unauthorize", timeout=30)
    for link in re.findall(r'https://pan\.quark\.cn/[^\s<>\]\)\"\']+', str(result.get("msg") or "")):
        parsed = urlsplit(link)
        if parsed.hostname == "pan.quark.cn" and parsed.path.startswith("/open/"):
            return {"authorization_url": link, "message": "请在夸克官方页面确认解除授权，完成后重新检查授权状态"}
    data = result.get("data") or {}
    link = data.get("revokeUrl")
    if isinstance(link, str):
        parsed = urlsplit(link)
        if parsed.scheme == "https" and parsed.hostname == "pan.quark.cn" and parsed.path.startswith("/open/"):
            return {"authorization_url": link, "message": "使用夸克网盘独立 App 扫码并确认解除授权"}
    for value in data.values():
        if isinstance(value, str) and value.startswith("https://pan.quark.cn/open/"):
            return {"authorization_url": value, "message": "请在夸克官方页面确认解除授权"}
    raise QuarkAuthError("获取夸克解除授权页面失败，请在夸克 App 的登录授权管理中操作")


async def complete_authorization(user_id: int, code: str) -> dict:
    code = code.strip()
    if not AUTH_CODE_PATTERN.fullmatch(code):
        raise QuarkAuthError("授权码格式不正确，请复制授权完成后地址中的 code 值")
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise QuarkAuthError("当前夸克任务正在执行，请完成后再授权")
    async with lock:
        result = await _run_cli(user_id, "login", "--token", code, timeout=35)
        if result.get("code") != 0:
            raise QuarkAuthError("授权码无效或已过期，请重新获取授权链接")
        if not _has_account(user_id):
            raise QuarkAuthError("夸克授权凭证未能保存，请检查本地数据目录后重试")
    return {"connected": True}
