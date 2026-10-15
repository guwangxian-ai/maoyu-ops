"""Thin adapters over the project's user-scoped cloud-drive clients."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from app.services import baidu_pcs_cli as bc, baidu_pcs_drive as bd, baidu_pcs_auth as ba
from app.services import quark_drive as qd, quark_drive_auth as qa
from app.services.baidu_drive_auth import exclusive
from app.services.cloud_asset_storage import AssetError, describe, filename, maximum

PROVIDERS = ("quark", "baidu")
LABELS = {"quark": "夸克网盘", "baidu": "百度网盘"}


def account_id(owner: int, provider: str) -> str | None:
    if provider == "quark":
        return qa._account_key(owner)
    if provider == "baidu":
        return (ba.account(owner) or {}).get("account_id")
    raise AssetError("不支持的网盘")


def binding(owner: int, target: dict) -> None:
    if not target.get("account_id") or account_id(owner, target["provider"]) != target["account_id"]:
        raise AssetError("此资料副本绑定的网盘账号已切换，请切回原账号后操作")


async def status(owner: int) -> dict:
    async def one(provider):
        try:
            data = await (qa.get_status(owner) if provider == "quark" else ba.get_status(owner))
            return {"provider": provider, **{key: data.get(key) for key in ("connected", "verified", "message")},
                    "share_notice": ""}
        except (qa.QuarkAuthError, ba.BaiduDriveError) as exc:
            return {"provider": provider, "connected": False, "verified": False, "message": str(exc)}
    return {"providers": await asyncio.gather(*(one(provider) for provider in PROVIDERS)),
            "limits": {"max_files": 100, "max_upload_bytes": maximum(), "scope": "项目管理的资料副本"}}


async def _quark_list(owner: int, parent: str) -> list[dict]:
    result = await qa._run_cli(owner, "browse", "--parent-fid", parent, "--page-size", "100", "--all", timeout=75)
    qd._check_result(result, "核对夸克资料副本失败")
    return qd._read_file_artifact(owner, result.get("_artifact"))


async def _quark_folder(owner: int, parent: str, name: str) -> str:
    rows = [row for row in await _quark_list(owner, parent) if row.get("filename") == name]
    if not rows:
        result = await qa._run_cli(owner, "create-folder", "--dir-path", name, "--parent-fid", parent, timeout=60)
        qd._check_result(result, "创建同步资料目录失败")
        rows = [row for row in await _quark_list(owner, parent) if row.get("filename") == name]
    if len(rows) != 1 or rows[0].get("category") != 0:
        raise AssetError("同步资料目录同名或无法确认，请在网盘检查")
    return qd._validate_fid(rows[0]["fid"])


def _same_file(remote: dict, row: dict) -> bool:
    if int(remote.get("size", -1)) != row["size"]:
        return False
    # Quark content_hash is provider-defined; it is not an MD5 digest.
    md5 = remote.get("md5")
    return not md5 or str(md5).lower() == row["md5"]


async def _quark_download(owner: int, fid: str, base: Path, reserved: int = 0) -> Path:
    """Caller holds the account lock. Bound disk use while the CLI is running."""
    async def progress(event):
        data = event.get("data") or {}
        if event.get("type") == "progress" and int(data.get("total") or 0) + reserved > maximum():
            raise AssetError("来源文件超过同步大小限制，已停止下载")
    call = asyncio.create_task(qa._run_cli(owner, "download", "--fid", qd._validate_fid(fid),
        "--output-dir", str(base), timeout=900, on_event=progress))
    try:
        while not call.done():
            await asyncio.wait({call}, timeout=0.5)
            paths = list(base.rglob("*"))
            if any(p.is_symlink() for p in paths) or reserved + sum(p.stat().st_size for p in paths if p.is_file()) > maximum():
                raise AssetError("来源下载超过大小限制或包含不安全文件，已停止")
        data = qd._check_result(await call, "夸克文件下载失败")
        saved = Path(data.get("filePath", ""))
        if saved.is_symlink() or not saved.resolve().is_relative_to(base.resolve()) or not saved.is_file():
            raise AssetError("仅支持普通文件，请将目录打包后导入")
        if reserved + saved.stat().st_size > maximum():
            raise AssetError("来源文件超过同步大小限制")
        return saved
    finally:
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)


async def _verify_remote(owner: int, provider: str, remote: dict, row: dict, base: Path) -> str:
    if not _same_file(remote, row):
        raise AssetError("网盘副本大小或 MD5 不一致，停止覆盖")
    if remote.get("md5"):
        return "md5"
    # Existing files after an interrupted upload must be checked by content,
    # including providers that only expose an opaque content_hash.
    with tempfile.TemporaryDirectory(prefix="verify-", dir=base.parent) as folder:
        directory = Path(folder)
        if provider == "quark":
            local = await _quark_download(owner, remote["fid"], directory)
        else:
            local = directory / filename(row["name"])
            await bd.download_to(owner, remote["path"], local, maximum())
        actual = await asyncio.to_thread(describe, local)
        if actual["size"] != row["size"] or actual["sha256"] != row["sha256"]:
            raise AssetError("网盘副本内容校验不一致，停止分享")
        return "download-sha256"


async def upload(owner: int, asset_id: str, version: int, base: Path, manifest: list[dict], target: dict, checkpoint) -> dict:
    provider = target["provider"]
    completed = []
    if provider == "quark":
        lock = qa._user_locks.setdefault(owner, asyncio.Lock())
        if lock.locked():
            raise AssetError("夸克当前有其他任务，请完成后重试同步")
        async with lock:
            binding(owner, target)
            parent = "0"
            for name in ("闲鱼同步资料", asset_id, f"v{version}"):
                parent = await _quark_folder(owner, parent, name)
                await checkpoint({"folder": parent})
            for row in manifest:
                local = base / filename(row["name"])
                if (await asyncio.to_thread(describe, local))["sha256"] != row["sha256"]:
                    raise AssetError("暂存资料已变化，停止同步")
                existing = [remote for remote in await _quark_list(owner, parent) if remote.get("filename") == row["name"]]
                if not existing:
                    result = await qa._run_cli(owner, "upload", str(local), "--parent-fid", parent, timeout=3600)
                    qd._check_result(result, "上传夸克失败，已提交结果请先核验")
                    existing = [remote for remote in await _quark_list(owner, parent) if remote.get("filename") == row["name"]]
                if len(existing) != 1 or not _same_file(existing[0], row):
                    raise AssetError("夸克副本同名或大小/校验值不一致，停止覆盖")
                remote = existing[0]
                verification = await _verify_remote(owner, provider, remote, row, base)
                completed.append({"name": row["name"], "size": row["size"], "fid": remote["fid"],
                                  "sha256": row["sha256"], "verification": verification})
                await checkpoint({"files": completed, "folder": parent})
            return {"files": completed, "folder": parent}
    async with exclusive(owner):
        binding(owner, target)
        await ba.require_account(owner)
        parent = bc.ROOT
        for name in ("闲鱼同步资料", asset_id, f"v{version}"):
            rows = [row for row in await bd._directory(owner, parent) if row["filename"] == name]
            if not rows:
                await bc.run(owner, "mkdir", {"path": parent.rstrip("/") + "/" + name})
                rows = [row for row in await bd._directory(owner, parent) if row["filename"] == name]
            if len(rows) != 1 or not rows[0]["isdir"]:
                raise AssetError("百度同步目录同名或无法确认")
            parent = rows[0]["path"]
            await checkpoint({"folder": parent})
        for row in manifest:
            local = base / filename(row["name"])
            if (await asyncio.to_thread(describe, local))["sha256"] != row["sha256"]:
                raise AssetError("暂存资料已变化，停止同步")
            existing = [remote for remote in await bd._directory(owner, parent) if remote["filename"] == row["name"]]
            if not existing:
                await bc.run(owner, "upload", {"local_path": str(local), "path": parent.rstrip("/") + "/" + row["name"]}, timeout=3600)
                existing = [remote for remote in await bd._directory(owner, parent) if remote["filename"] == row["name"]]
            if len(existing) != 1 or not _same_file(existing[0], row):
                raise AssetError("百度副本同名或大小/校验值不一致，停止覆盖")
            remote = existing[0]
            verification = await _verify_remote(owner, provider, remote, row, base)
            completed.append({"name": row["name"], "size": row["size"], "path": remote["path"], "fsid": remote["fs_id"],
                              "sha256": row["sha256"], "verification": verification})
            await checkpoint({"files": completed, "folder": parent})
        return {"files": completed, "folder": parent}


def expiration(created_at: str, period: int) -> str | None:
    if not period:
        return None
    return (datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(days=period)).isoformat()


async def share(owner: int, name: str, target: dict, period: int) -> dict:
    binding(owner, target)
    ids = [row["fid"] for row in target["files"]] if target["provider"] == "quark" else [row["fsid"] for row in target["files"]]
    records = (await (qd.list_shares(owner) if target["provider"] == "quark" else bd.list_shares(owner)))["shares"]
    expired_type = {0: 1, 1: 2, 7: 3, 30: 4}[period]
    for record in records:
        keys = record.get("fids", []) if target["provider"] == "quark" else record.get("fsids", [])
        actual_period = {1: 0, 2: 1, 3: 7, 4: 30}.get(record.get("expired_type")) if target["provider"] == "quark" else record.get("period")
        if target["provider"] == "baidu" and (record.get("status") != "active" or not isinstance(record.get("created_at"), str)):
            continue
        until = expiration(record["created_at"], period) if actual_period == period else None
        if set(keys) == set(ids) and actual_period == period and (until is None or datetime.fromisoformat(until) > datetime.now(timezone.utc) + timedelta(minutes=5)):
            return {"share_url": record["share_url"], "passcode": record.get("passcode", ""), "expires_at": until, "period": period}
    if target.get("share_outcome_unknown") or target.get("status") == "unknown" and target.get("last_operation") == "shares":
        raise AssetError("上次分享结果未确认且没有保存链接，请先在原网盘核验，停止重复创建")
    binding(owner, target)
    result = await (qd.create_share(owner, ids, name, 2, expired_type) if target["provider"] == "quark"
                    else bd.create_share(owner, ids, name, period, paths=[row["path"] for row in target["files"]]))
    record = result["share"]
    return {"share_url": record["share_url"], "passcode": record.get("passcode", ""),
            "expires_at": expiration(record["created_at"], period), "period": period}


async def import_files(owner: int, target: dict, base: Path, file_ids: list[str], paths: list[str]) -> list[dict]:
    """Download selected ordinary files once, then upload the same staged bytes."""
    binding(owner, target)
    manifest = []
    if target["provider"] == "quark":
        async with qa._user_locks.setdefault(owner, asyncio.Lock()):
            binding(owner, target)
            for fid in file_ids:
                with tempfile.TemporaryDirectory(prefix="import-", dir=base.parent) as folder:
                    saved = await _quark_download(owner, fid, Path(folder), sum(row["size"] for row in manifest))
                    destination = base / filename(saved.name)
                    if destination.exists() or any(row["name"].casefold() == saved.name.casefold() for row in manifest):
                        raise AssetError("导入文件同名，请分开导入")
                    shutil.move(saved, destination)
                    manifest.append(await asyncio.to_thread(describe, destination))
    else:
        async with exclusive(owner):
            binding(owner, target)
            await ba.require_account(owner)
            for path in paths:
                path = bd.validate_path(path, allow_root=False)
                metadata = await bd._metadata(owner, path)
                if metadata["isdir"]:
                    raise AssetError("仅支持导入普通文件，请将目录打包后导入")
                destination = base / filename(metadata["filename"])
                if destination.exists() or any(row["name"].casefold() == destination.name.casefold() for row in manifest):
                    raise AssetError("导入文件同名，请分开导入")
                remaining = maximum() - sum(row["size"] for row in manifest)
                if metadata["size"] > remaining:
                    raise AssetError("导入资料累计超过同步大小限制")
                with tempfile.TemporaryDirectory(prefix="import-", dir=base.parent) as folder:
                    temporary = Path(folder)
                    downloaded = temporary / destination.name
                    await bd.download_to(owner, path, downloaded, remaining)
                    actual = await asyncio.to_thread(describe, downloaded)
                    if actual["size"] != metadata["size"] or actual["size"] > remaining:
                        raise AssetError("来源下载大小不一致或超过同步限制")
                    shutil.move(downloaded, destination)
                manifest.append(await asyncio.to_thread(describe, destination))
    if not manifest or len({row["name"].casefold() for row in manifest}) != len(manifest) or sum(row["size"] for row in manifest) > maximum():
        raise AssetError("导入资料为空、同名或超过大小限制")
    return manifest


def validate_share_source(source: dict) -> None:
    url = source.get("url", "")
    provider = source["provider"]
    host = "pan.quark.cn" if provider == "quark" else "pan.baidu.com"
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme == "https" and parsed.hostname == host and parsed.port in {None, 443} and not parsed.username and not parsed.password and not parsed.fragment and any(
            parsed.path.startswith(prefix) and len(parsed.path) > len(prefix) for prefix in ("/s/", "/share/"))
    except ValueError:
        valid = False
    if not valid or any(char.isspace() or char == "\\" for char in url):
        raise AssetError("请使用所选网盘的官方分享链接")


async def transfer_source(owner: int, asset_id: str, source: dict) -> dict:
    """Native transfer on the source provider, then stage its saved ordinary files."""
    validate_share_source(source)
    url, provider = source["url"], source["provider"]
    binding(owner, source)
    if provider == "baidu" and "share-transfer" not in bd.OPERATIONS:
        raise AssetError("当前百度接入暂不支持分享转存，请从文件管理选择已有文件，或上传本地资料")
    if provider == "quark":
        async with qa._user_locks.setdefault(owner, asyncio.Lock()):
            binding(owner, source)
            parent = await _quark_folder(owner, "0", "闲鱼来源转存")
            parent = await _quark_folder(owner, parent, asset_id)
            args = ["--url", url, "--to-pdir-fid", parent]
            if source.get("password"):
                args += ["--passcode", source["password"]]
            result = await qa._run_cli(owner, "saveas", *args, timeout=900)
            data = qd._check_result(result, "来源分享转存失败，已提交操作请在网盘核验")
            if data.get("status") != 2 or data.get("result_status") == "OUTCOME_UNKNOWN" or data.get("fail_count"):
                raise AssetError("来源转存未全部确认，停止下载与同步")
            files = await _quark_list(owner, parent)
            if not files or len(files) > 100 or any(row.get("category") == 0 for row in files):
                raise AssetError("暂支持分享中的普通文件，目录请打包后导入")
            if sum(int(row.get("size") or 0) for row in files) > maximum():
                raise AssetError("来源分享超过同步大小限制")
            return {**source, "file_ids": [row["fid"] for row in files], "paths": []}
    async with exclusive(owner):
        binding(owner, source)
        parent = bc.ROOT
        for name in ("闲鱼来源转存", asset_id):
            rows = [row for row in await bd._directory(owner, parent) if row["filename"] == name]
            if not rows:
                await bc.run(owner, "mkdir", {"path": parent.rstrip("/") + "/" + name})
                rows = [row for row in await bd._directory(owner, parent) if row["filename"] == name]
            if len(rows) != 1 or not rows[0]["isdir"]:
                raise AssetError("来源转存目录无效")
            parent = rows[0]["path"]
    binding(owner, source)
    result = await bd.run_operation(owner, "share-transfer", {"url": url, "password": source.get("password", ""), "target_dir": parent, "confirmed": True})
    if result.get("status") in {"submitted", "partial", "failed"}:
        raise AssetError("来源转存尚未全部确认，已提交任务请在百度网盘核验；不会重复转存")
    listing = await bd.list_files(owner, parent)
    files = listing["files"]
    if listing.get("has_more") or not files or len(files) > 100 or any(row["isdir"] for row in files) or sum(row["size"] for row in files) > maximum():
        raise AssetError("暂支持大小限制内的分享普通文件，目录请打包后导入")
    if not isinstance(result.get("count"), int) or result["count"] != len(files):
        raise AssetError("来源转存数量未全部核验，停止下载与同步")
    return {**source, "paths": [row["path"] for row in files], "file_ids": []}
