"""User-scoped file and native share operations over BaiduPCS-Go.

Only the project's private JSON bridge talks to Baidu. Public responses never
contain login cookies, download tokens, or raw provider error payloads.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import UploadFile

from app.services import baidu_pcs_auth as auth, baidu_pcs_cli as cli
from app.services.baidu_drive_auth import BaiduDriveError, exclusive, read_private, user_dir, write_private
from app.services.baidu_drive_skill import CATEGORIES, EXTENSIONS, _ids, validate_name
from app.services.baidu_share_guard import ensure_unreferenced

OPERATIONS = ("create-folder", "copy", "move", "rename", "delete", "file-meta", "upload-text")


def validate_path(value: str, *, allow_root: bool = True) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value or len(value.encode()) > 4096:
        raise BaiduDriveError("请提供以 / 开头的网盘绝对路径")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BaiduDriveError("网盘路径不能包含控制字符")
    value = value.rstrip("/") or "/"
    if value != "/" and any(part in {"", ".", "..", "~"} for part in value[1:].split("/")):
        raise BaiduDriveError("网盘路径不能包含空目录、.、.. 或 ~")
    if not allow_root and value == "/":
        raise BaiduDriveError("不能对网盘根目录执行此操作")
    return value


def file_view(row: dict) -> dict:
    if not isinstance(row, dict):
        raise BaiduDriveError("百度返回的文件记录无效")
    path = validate_path(row.get("path", ""), allow_root=False)
    fsid = str(row.get("fs_id", row.get("fsid", "")))
    _ids([fsid])
    name = str(row.get("filename") or row.get("server_filename") or PurePosixPath(path).name)
    try:
        stamp = float(row.get("server_mtime", row.get("mtime", row.get("updated_at", 0))) or 0)
        suffix = PurePosixPath(name).suffix.lower().lstrip(".")
        return {"fs_id": fsid, "path": path, "filename": name,
                "size": max(0, int(row.get("size") or 0)),
                "isdir": row.get("isdir") in {True, 1, "1"},
                "category": int(row.get("category") or next((key for key, extensions in EXTENSIONS.items() if suffix in extensions), 6)),
                "updated_at": int(stamp * 1000 if stamp < 10**12 else stamp),
                **({"md5": row["md5"]} if isinstance(row.get("md5"), str) and re.fullmatch(r"[a-fA-F0-9]{32}", row["md5"]) else {})}
    except (TypeError, ValueError, OverflowError) as exc:
        raise BaiduDriveError("百度返回的文件属性格式无效") from exc


def _records_path(user_id: int, account_id: str, name: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", account_id):
        raise BaiduDriveError("百度账号连接记录无效")
    return user_dir(user_id) / "pcs/connections" / account_id / name


def _cache(user_id: int, record: dict, rows: list[dict]) -> None:
    path = _records_path(user_id, record["account_id"], "files.json")
    old = read_private(path, {})
    if not isinstance(old, dict):
        old = {}
    merged = {row["fs_id"]: row["path"] for row in rows}
    merged.update({key: value for key, value in old.items() if key not in merged})
    write_private(path, dict(list(merged.items())[:10000]))


async def _list(user_id: int, directory: str, page: int = 1, page_size: int = 1000) -> dict:
    data = await cli.run(user_id, "list", {"dir": validate_path(directory), "page": page, "page_size": page_size})
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BaiduDriveError("百度未返回有效文件列表")
    return {"files": [file_view(row) for row in data["files"]], "has_more": bool(data.get("has_more"))}


async def _directory(user_id: int, directory: str) -> list[dict]:
    rows, seen = [], set()
    for page in range(1, 11):
        data = await _list(user_id, directory, page)
        batch = data["files"]
        if any(row["fs_id"] in seen for row in batch):
            raise BaiduDriveError("百度返回了重复分页，请刷新后重试")
        rows.extend(batch)
        seen.update(row["fs_id"] for row in batch)
        if not data["has_more"]:
            return rows
    raise BaiduDriveError("目录超过 10,000 项，请进入子目录后操作")


async def _metadata(user_id: int, path: str) -> dict:
    path = validate_path(path, allow_root=False)
    data = await cli.run(user_id, "meta", {"paths": [path]})
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BaiduDriveError("百度未返回有效文件信息")
    rows = [file_view(row) for row in data["files"]]
    if len(rows) != 1 or rows[0]["path"] != path:
        raise BaiduDriveError("文件不存在或路径已变化，请刷新列表")
    return rows[0]


async def _resolve(user_id: int, record: dict, paths: list[str], fsids: list[str]) -> list[dict]:
    if fsids:
        fsids = _ids(fsids)
    if paths:
        if not 1 <= len(paths) <= 100:
            raise BaiduDriveError("请选择 1 到 100 个文件")
        paths = list(dict.fromkeys(validate_path(path, allow_root=False) for path in paths))
    else:
        ids = _ids(fsids)
        cached = read_private(_records_path(user_id, record["account_id"], "files.json"), {})
        if not isinstance(cached, dict) or any(fsid not in cached for fsid in ids):
            raise BaiduDriveError("请先查询文件列表，或直接提供 files 的 paths")
        paths = [validate_path(cached[fsid], allow_root=False) for fsid in ids]
    data = await cli.run(user_id, "meta", {"paths": paths})
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BaiduDriveError("百度未返回有效文件信息")
    rows = [file_view(row) for row in data["files"]]
    by_path = {row["path"]: row for row in rows}
    if len(by_path) != len(paths) or any(path not in by_path for path in paths):
        raise BaiduDriveError("文件已变化，请刷新列表后重新选择")
    result = [by_path[path] for path in paths]
    if fsids and {row["fs_id"] for row in result} != set(fsids):
        raise BaiduDriveError("文件 ID 与路径不一致，请刷新列表")
    _cache(user_id, record, result)
    return result


async def list_files(user_id: int, directory: str = "/", keyword: str = "", page: int = 1,
                     page_size: int = 100, category: str = "all") -> dict:
    directory = validate_path(directory)
    if not 1 <= page <= 10000 or not 1 <= page_size <= 100 or len(keyword) > 100 or category not in {"all", "folder", *CATEGORIES}:
        raise BaiduDriveError("分页、分类或搜索参数无效")
    if any(ord(char) < 32 for char in keyword):
        raise BaiduDriveError("搜索词不能包含控制字符")
    async with cli.read_lock(user_id):
        record = await auth.require_account(user_id)
        if keyword.strip():
            # Fetch a bounded search result once so category pagination remains
            # consistent. The bridge refuses a silently truncated result.
            data = await cli.run(user_id, "search", {"dir": directory, "keyword": keyword.strip(),
                                 "page": 1, "page_size": 10000, "max_items": 10000}, timeout=90)
            if not isinstance(data, dict) or not isinstance(data.get("files"), list) or data.get("truncated"):
                raise BaiduDriveError("搜索结果过多或不完整，请缩小目录或关键词")
            rows = [file_view(row) for row in data["files"]]
        elif category == "all":
            data = await _list(user_id, directory, page, page_size)
            rows = data["files"]
            _cache(user_id, record, rows)
            return {"files": rows, "dir": directory, "keyword": "", "page": page,
                    "page_size": page_size, "has_more": data["has_more"], "total": len(rows), "search_scope": "directory"}
        else:
            rows = await _directory(user_id, directory)
        filtered = rows if category == "all" else [row for row in rows if row["isdir"]] if category == "folder" else [
            row for row in rows if not row["isdir"] and row["category"] == CATEGORIES[category]]
        offset = (page - 1) * page_size
        files = filtered[offset:offset + page_size]
        _cache(user_id, record, files)
        return {"files": files, "dir": directory, "keyword": keyword.strip(), "page": page,
                "page_size": page_size, "has_more": len(filtered) > offset + page_size,
                "total": len(filtered), "search_scope": "recursive-directory" if keyword.strip() else "directory"}


def _share_url(value: str) -> str:
    link = cli.trusted_url(value)
    if urlsplit(link).hostname != "pan.baidu.com" or not urlsplit(link).path.startswith(("/s/", "/share/")):
        raise BaiduDriveError("百度未返回有效的原生分享链接")
    return link


def _saved_shares(user_id: int, record: dict) -> tuple[Path, list[dict]]:
    path = _records_path(user_id, record["account_id"], "shares.json")
    old = read_private(path, [])
    if not isinstance(old, list):
        raise BaiduDriveError("项目分享记录格式无效")
    return path, old


async def list_shares(user_id: int, page: int = 1) -> dict:
    if not 1 <= page <= 10000:
        raise BaiduDriveError("分享分页参数无效")
    async with cli.read_lock(user_id):
        record = await auth.require_account(user_id)
        _, saved = _saved_shares(user_id, record)
        by_id = {str(row.get("id")): row for row in saved if isinstance(row, dict)}
        data = await cli.run(user_id, "share-list", {"page": page}, timeout=90)
        if not isinstance(data, dict) or not isinstance(data.get("shares"), list):
            raise BaiduDriveError("百度未返回有效的账号分享列表")
        shares = []
        for raw in data["shares"]:
            if not isinstance(raw, dict):
                raise BaiduDriveError("百度返回的分享记录无效")
            share_id = str(raw.get("shareId", raw.get("id", "")))
            _ids([share_id])
            local = by_id.get(share_id, {})
            link = raw.get("shorturl") or raw.get("shortlink") or raw.get("share_url") or local.get("share_url", "")
            if isinstance(link, str) and link.startswith("pan.baidu.com/"):
                link = "https://" + link
            try:
                link = _share_url(link)
            except BaiduDriveError:
                link = ""
            remaining = int(raw.get("expiredTime", 0) or 0)
            invalid = int(raw.get("expiredType", 0) or 0) == -1 or remaining < 0
            expires_at = int((datetime.now(timezone.utc).timestamp() + remaining) * 1000) if 0 < remaining < 10 * 366 * 86400 else None
            period = local.get("period", 0 if remaining == 0 and not invalid else -1)
            code = str(raw.get("pwd") or raw.get("passcode") or local.get("passcode") or "")
            if code and not re.fullmatch(r"[a-zA-Z0-9]{4}", code):
                code = ""
            state = "expired" if invalid else "active"
            if not link or int(raw.get("public", 0) or 0) == 0 and not code:
                state = "unavailable" if not invalid else state
            fsids = [str(value) for value in raw.get("fsIds", raw.get("fsids", []))]
            for fsid in fsids:
                _ids([fsid])
            typical = raw.get("typicalPath", "")
            title = local.get("title") or (PurePosixPath(typical).name if isinstance(typical, str) and typical else "百度网盘分享")
            shares.append({"id": share_id, "title": title, "fsids": fsids,
                           "paths": local.get("paths", []), "share_url": link, "passcode": code,
                           "period": period, "created_at": local.get("created_at"), "expires_at": expires_at, "status": state})
        index_path = _records_path(user_id, record["account_id"], "share-pages.json")
        old_index = read_private(index_path, {})
        if not isinstance(old_index, dict):
            old_index = {}
        index = {row["id"]: page for row in shares}
        index.update({key: value for key, value in old_index.items() if key not in index})
        write_private(index_path, dict(list(index.items())[:5000]))
        history_path, history = _saved_shares(user_id, record)
        current_ids = {row["id"] for row in shares}
        # History enriches provider-returned rows only. It never substitutes for
        # the live account list, and unknown creation dates stay unknown.
        write_private(history_path, [*shares, *(row for row in history if str(row.get("id")) not in current_ids)][:5000])
        return {"shares": shares, "total": len(shares), "scope": "account", "page": page,
                "page_size": 100, "has_more": bool(data.get("has_more"))}


async def share_detail(user_id: int, share_id: str) -> dict:
    """Only retrieve details for a share in this account's current cloud list."""
    _ids([share_id])
    async with cli.read_lock(user_id):
        record = await auth.require_account(user_id)
        index = read_private(_records_path(user_id, record["account_id"], "share-pages.json"), {})
        page = index.get(share_id) if isinstance(index, dict) else None
        if not isinstance(page, int) or not 1 <= page <= 10000:
            raise BaiduDriveError("请先查询当前账号分享列表，再读取此分享的提取码")
        listing = await cli.run(user_id, "share-list", {"page": page}, timeout=90)
        rows = listing.get("shares", []) if isinstance(listing, dict) else []
        raw = next((row for row in rows if isinstance(row, dict) and str(row.get("shareId", row.get("id"))) == share_id), None)
        if raw is None:
            raise BaiduDriveError("此分享不在当前账号列表中，请刷新列表后重试")
        if int(raw.get("expiredType", 0) or 0) == -1 or int(raw.get("expiredTime", 0) or 0) < 0:
            raise BaiduDriveError("此分享已经失效，请重新创建分享")
        result = await cli.run(user_id, "share-detail", {"id": share_id})
        if not isinstance(result, dict) or str(result.get("id")) != share_id:
            raise BaiduDriveError("百度未返回此分享的有效详情")
        link = _share_url(result.get("share_url") or result.get("shorturl") or raw.get("shortlink", ""))
        code = result.get("passcode", result.get("pwd", ""))
        if not isinstance(code, str) or code and not re.fullmatch(r"[A-Za-z0-9]{4}", code):
            raise BaiduDriveError("百度未返回有效提取码，请在原网盘检查")
        if int(raw.get("public", 0) or 0) == 0 and not code:
            raise BaiduDriveError("百度未返回私密分享的提取码，请在原网盘检查")
        path, saved = _saved_shares(user_id, record)
        cached = next((row for row in saved if str(row.get("id")) == share_id), None)
        details = {"id": share_id, "share_url": link, "passcode": code, "status": "active"}
        if cached:
            cached.update(details)
        else:
            # Cache only provider-returned details; no invented creation time.
            saved.insert(0, {**details, "fsids": [str(value) for value in raw.get("fsIds", [])],
                             "title": PurePosixPath(raw.get("typicalPath") or "/").name or "百度网盘分享"})
        write_private(path, saved[:5000])
        return {"share": details}


async def create_share(user_id: int, fsids: list[str], title: str, period: int = 7,
                       password: str = "", paths: list[str] | None = None) -> dict:
    if period not in {0, 1, 7, 30} or not title.strip() or len(title) > 100:
        raise BaiduDriveError("分享名称或有效期无效")
    if password and not re.fullmatch(r"[A-Za-z0-9]{4}", password):
        raise BaiduDriveError("提取码须为 4 位字母或数字")
    password = password or "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4))
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        files = await _resolve(user_id, record, paths or [], fsids)
        data = await cli.run(user_id, "share-create", {"paths": [row["path"] for row in files],
                             "password": password, "period": period}, timeout=90)
        if not isinstance(data, dict):
            raise BaiduDriveError("未能确认分享结果，请在百度网盘核验后再操作")
        share_id = str(data.get("id", ""))
        _ids([share_id])
        link = _share_url(data.get("share_url", ""))
        code = data.get("passcode", "")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{4}", code):
            raise BaiduDriveError("分享已提交但提取码未确认，请在百度网盘核验")
        now = datetime.now(timezone.utc)
        share = {"id": share_id, "title": title.strip(), "fsids": [row["fs_id"] for row in files],
                 "paths": [row["path"] for row in files], "share_url": link, "passcode": code,
                 "period": period, "created_at": now.isoformat(), "status": "active",
                 "expires_at": int((now + timedelta(days=period)).timestamp() * 1000) if period else None}
        try:
            path, old = _saved_shares(user_id, record)
            write_private(path, [share, *(row for row in old if str(row.get("id")) != share_id)][:5000])
        except BaiduDriveError:
            return {"share": share, "saved": False, "message": "百度分享已创建，但项目记录保存失败，请立即复制链接"}
        return {"share": share, "saved": True}


async def cancel_share(user_id: int, share_id: str, confirmed: bool) -> dict:
    _ids([share_id])
    if confirmed is not True:
        raise BaiduDriveError("请先确认取消此分享")
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        path, old = _saved_shares(user_id, record)
        cached = next((row for row in old if str(row.get("id")) == share_id), None)
        if not cached:
            raise BaiduDriveError("请先查询当前账号分享列表，再取消此分享")
        link = _share_url(cached.get("share_url", ""))
        await ensure_unreferenced(user_id, link)
        await cli.run(user_id, "share-cancel", {"ids": [share_id]})
        for row in old:
            if str(row.get("id")) == share_id:
                row["status"] = "cancelled"
        write_private(path, old)
        return {"share_id": share_id, "cancelled": True, "message": "百度分享已取消"}


async def _no_collision(user_id: int, directory: str, name: str) -> None:
    if any(row["filename"] == name for row in await _directory(user_id, directory)):
        raise BaiduDriveError("目标已存在同名文件，请修改名称；项目不会覆盖现有内容")


async def run_operation(user_id: int, operation: str, payload: dict) -> dict:
    if operation not in OPERATIONS:
        raise BaiduDriveError("当前百度接入暂不支持此操作，请查看 capabilities")
    if operation != "file-meta" and payload.get("confirmed") is not True:
        raise BaiduDriveError("请先核对操作对象并确认执行")
    directory = validate_path(payload.get("dir") or "/")
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        if operation == "file-meta":
            return {"files": await _resolve(user_id, record, payload.get("paths") or [], payload.get("fsids") or []),
                    "message": "文件信息已查询"}
        if operation == "create-folder":
            name = validate_name(payload.get("name") or "")
            await _no_collision(user_id, directory, name)
            path = str(PurePosixPath(directory) / name)
            await cli.run(user_id, "mkdir", {"path": path})
            metadata = await _metadata(user_id, path)
            if not metadata["isdir"]:
                raise BaiduDriveError("创建文件夹结果未确认，请刷新核验")
            return {"status": "success", "path": path, "message": "文件夹已创建"}
        if operation == "upload-text":
            content = payload.get("content") or ""
            name = validate_name(payload.get("filename") or "文本资料.txt")
            if not 1 <= len(content) <= 20000:
                raise BaiduDriveError("文本长度须为 1 到 20,000 个字符")
            await _no_collision(user_id, directory, name)
            destination = str(PurePosixPath(directory) / name)
            with tempfile.TemporaryDirectory(dir=cli.config_path(user_id).parent) as temp:
                local = Path(temp) / name
                local.write_text(content, encoding="utf-8")
                local.chmod(0o600)
                await cli.run(user_id, "upload", {"local_path": str(local), "path": destination}, timeout=180)
            metadata = await _metadata(user_id, destination)
            _cache(user_id, record, [metadata])
            return {"status": "success", "path": destination, "fsid": metadata["fs_id"], "message": "文本文件已上传"}
        files = await _resolve(user_id, record, payload.get("paths") or [], payload.get("fsids") or [])
        target = validate_path(payload.get("target_dir") or "/")
        if operation == "rename" and len(files) != 1:
            raise BaiduDriveError("每次重命名请选择一个文件")
        if operation in {"copy", "move"} and target != "/" and not (await _metadata(user_id, target))["isdir"]:
            raise BaiduDriveError("目标路径必须是文件夹")
        results = []
        for row in files:
            source = row["path"]
            try:
                if operation == "delete":
                    await cli.run(user_id, "delete", {"paths": [source]}, timeout=180)
                    destination = source
                else:
                    parent = str(PurePosixPath(source).parent) if operation == "rename" else target
                    name = validate_name(payload.get("name") or "") if operation == "rename" else row["filename"]
                    destination = str(PurePosixPath(parent) / name)
                    if destination == source or row["isdir"] and destination.startswith(source + "/"):
                        raise BaiduDriveError("目标不能是原路径或文件夹的子目录")
                    await _no_collision(user_id, parent, name)
                    await cli.run(user_id, "copy" if operation == "copy" else "move",
                                  {"source": source, "destination": destination}, timeout=180)
                    metadata = await _metadata(user_id, destination)
                    _cache(user_id, record, [metadata])
                results.append({"path": source, "destination": destination, "status": "success"})
            except BaiduDriveError as exc:
                results.append({"path": source, "status": "failed", "message": str(exc)})
        completed = sum(row["status"] == "success" for row in results)
        return {"status": "success" if completed == len(results) else "partial" if completed else "failed",
                "results": results, "count": completed, "message": f"完成 {completed}/{len(results)} 项操作"}


async def upload_files(user_id: int, files: list[UploadFile], directory: str = "/") -> dict:
    directory = validate_path(directory)
    if not 1 <= len(files) <= 100:
        raise BaiduDriveError("每次上传 1 到 100 个文件")
    maximum = int(os.environ.get("BAIDU_MAX_UPLOAD_BYTES", str(1024**3)))
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        results, total = [], 0
        for file in files:
            name = file.filename or ""
            try:
                name = validate_name(name)
                await _no_collision(user_id, directory, name)
                with tempfile.TemporaryDirectory(dir=cli.config_path(user_id).parent) as temp:
                    local = Path(temp) / name
                    with local.open("wb") as stream:
                        local.chmod(0o600)
                        while chunk := await file.read(1024**2):
                            total += len(chunk)
                            if total > maximum:
                                raise BaiduDriveError("上传超出项目大小限制")
                            stream.write(chunk)
                    path = str(PurePosixPath(directory) / name)
                    await cli.run(user_id, "upload", {"local_path": str(local), "path": path}, timeout=3600)
                metadata = await _metadata(user_id, path)
                _cache(user_id, record, [metadata])
                results.append({"filename": name, "status": "success", "path": path, "fsid": metadata["fs_id"]})
            except (BaiduDriveError, OSError) as exc:
                results.append({"filename": name, "status": "failed", "message": str(exc) if isinstance(exc, BaiduDriveError) else "本地临时文件处理失败"})
        count = sum(row["status"] == "success" for row in results)
        return {"status": "success" if count == len(results) else "partial" if count else "failed",
                "results": results, "message": f"上传完成 {count}/{len(results)} 个文件"}


async def download_to(user_id: int, path: str, local: Path, maximum: int) -> None:
    """Caller owns the account lock and provides a project-private destination."""
    await cli.run(user_id, "download", {"path": validate_path(path, allow_root=False),
                  "local_path": str(local), "max_bytes": maximum}, timeout=900,
                  disk_directory=local.parent, disk_limit=maximum)
    if local.is_symlink() or not local.is_file() or local.stat().st_size > maximum:
        raise BaiduDriveError("百度未保存完整文件或文件超过下载限制")


async def download(user_id: int, path: str) -> tuple[Path, str, Path]:
    path = validate_path(path, allow_root=False)
    async with exclusive(user_id):
        await auth.require_account(user_id)
        metadata = await _metadata(user_id, path)
        name = validate_name(metadata["filename"])
        maximum = int(os.environ.get("BAIDU_MAX_DOWNLOAD_BYTES", str(256 * 1024**2)))
        if metadata["isdir"]:
            raise BaiduDriveError("当前支持下载普通文件，请在网盘打包目录后下载")
        if metadata["size"] > maximum:
            raise BaiduDriveError("文件超过项目下载限制，请直接在百度网盘下载")
        temp = Path(tempfile.mkdtemp(dir=cli.config_path(user_id).parent, prefix="download-"))
        local = temp / name
        try:
            await download_to(user_id, path, local, maximum)
            if local.stat().st_size != metadata["size"]:
                raise BaiduDriveError("下载文件大小不一致，请重试")
            return local, name, temp
        except BaseException:
            shutil.rmtree(temp, ignore_errors=True)
            raise
