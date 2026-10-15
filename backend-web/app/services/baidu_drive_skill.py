"""Project HTTP operations implemented solely by the official Skill's bdpan CLI."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import UploadFile

from app.services import baidu_drive_cli as cli
from app.services import baidu_drive_skill_auth as auth
from app.services.baidu_drive_auth import BaiduDriveError, exclusive, read_private, user_dir, write_private

OPERATIONS = ("create-folder", "copy", "move", "rename", "delete", "file-meta", "upload-text",
              "share-transfer", "share-transfer-list", "share-transfer-select")
CATEGORIES = {"video": 1, "audio": 2, "image": 3, "doc": 4, "app": 5, "other": 6}
EXTENSIONS = {
    1: {"mp4", "mkv", "mov", "avi", "rmvb", "webm", "m4v", "wmv"},
    2: {"mp3", "wav", "flac", "aac", "ogg", "m4a", "ape"},
    3: {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic"},
    4: {"txt", "md", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "rtf"},
    5: {"apk", "exe", "dmg", "ipa", "msi"},
}


def validate_path(value: str, *, allow_root: bool = True) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096 or "\\" in value or not value.startswith("/"):
        raise BaiduDriveError("网盘路径必须是 /apps/bdpan 范围内的绝对路径")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BaiduDriveError("网盘路径不能包含控制字符")
    value = value.rstrip("/") or "/"
    if value == "/":
        value = cli.ROOT  # Existing UI's virtual root.
    if value != cli.ROOT and not value.startswith(cli.ROOT + "/"):
        raise BaiduDriveError("官方 Skill 仅能管理「我的应用数据/bdpan」目录")
    if any(part in {"", ".", "..", "~"} for part in value[1:].split("/")):
        raise BaiduDriveError("网盘路径不能包含空目录、.、.. 或 ~")
    if not allow_root and value == cli.ROOT:
        raise BaiduDriveError("不能对应用根目录执行此操作")
    return value


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not name or name in {".", "..", "~"} or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name) or len(name.encode()) > 255:
        raise BaiduDriveError("文件名称无效，不能含路径分隔符或特殊字符，且不能超过 255 字节")
    return name


def _ids(values: list[str]) -> list[str]:
    if not 1 <= len(values) <= 100 or any(not isinstance(x, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", x) for x in values):
        raise BaiduDriveError("请选择 1 到 100 个有效文件 ID")
    return list(dict.fromkeys(values))


def _links(data: dict) -> dict:
    result = {}
    if isinstance(data.get("return_url"), str):
        try:
            result["return_url"] = cli.trusted_url(data["return_url"])
            result["return_hint"] = "点击查看"
            markdown = data.get("return_markdown")
            if isinstance(markdown, str) and re.fullmatch(r"\[[^\]\r\n]*\]\(https://[^\s()]+\)", markdown) and markdown.endswith("(" + result["return_url"] + ")"):
                result["return_markdown"] = markdown
        except BaiduDriveError:
            pass
    return result


def file_view(row: dict) -> dict:
    if not isinstance(row, dict):
        raise BaiduDriveError("官方 CLI 未返回有效文件记录")
    path = row.get("path", row.get("remote_path", ""))
    if isinstance(path, str) and path.startswith("我的应用数据/bdpan"):
        path = "/apps/bdpan" + path[len("我的应用数据/bdpan"):]
    path = validate_path(path, allow_root=False)
    fsid = str(row.get("fs_id", row.get("fsid", "")))
    _ids([fsid])
    name = str(row.get("server_filename") or row.get("filename") or PurePosixPath(path).name)
    try:
        stamp = row.get("server_mtime", row.get("mtime", 0))
        if isinstance(stamp, str) and not stamp.replace(".", "", 1).isdigit():
            timestamp = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
        else:
            timestamp = float(stamp or 0)
            timestamp = int(timestamp * 1000 if timestamp < 10**12 else timestamp)
        isdir = row.get("isdir", row.get("is_dir")) in {True, 1, "1"}
        suffix = PurePosixPath(name).suffix.lower().lstrip(".")
        category = int(row.get("category") or next((key for key, values in EXTENSIONS.items() if suffix in values), 6))
        result = {"fs_id": fsid, "path": path, "filename": name, "size": max(0, int(row.get("size") or 0)),
                  "isdir": isdir, "category": category, "updated_at": timestamp, **_links(row)}
        if isinstance(row.get("md5"), str):
            result["md5"] = row["md5"]
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise BaiduDriveError("官方 CLI 返回的文件属性格式无效") from exc


def _records_path(user_id: int, account_id: str, name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise BaiduDriveError("账号连接记录无效")
    return user_dir(user_id) / "official/connections" / account_id / name


def _cache(user_id: int, record: dict, files: list[dict]) -> None:
    path = _records_path(user_id, record["account_id"], "files.json")
    previous = read_private(path, {})
    if not isinstance(previous, dict):
        previous = {}
    merged = {row["fs_id"]: row["path"] for row in files}
    merged.update({key: value for key, value in previous.items() if key not in merged})
    write_private(path, dict(list(merged.items())[:10000]))


async def _list(user_id: int, directory: str, start: int = 0, limit: int = 1000) -> list[dict]:
    directory = validate_path(directory)
    # bdpan's documented ls paths are relative to the app root. Passing the
    # absolute root returns that directory's metadata, not its children.
    relative = directory[len(cli.ROOT):].lstrip("/")
    args = ["ls", "--start", str(start), "--limit", str(limit), "--order", "name"]
    if relative:
        args += ["--", relative]
    result = await cli.run(user_id, *args)
    if not isinstance(result, list):
        raise BaiduDriveError("官方 CLI 未返回有效文件列表")
    return [file_view(row) for row in result]


async def _directory(user_id: int, directory: str) -> list[dict]:
    result = []
    for start in range(0, 10000, 1000):
        batch = await _list(user_id, directory, start, 1000)
        result.extend(batch)
        if len(batch) < 1000:
            return result
    raise BaiduDriveError("目录超过 10,000 项，请进入子目录后操作")


async def _metadata(user_id: int, path: str) -> dict:
    path = validate_path(path, allow_root=False)
    for row in await _directory(user_id, str(PurePosixPath(path).parent)):
        if row["path"] == path:
            return row
    raise BaiduDriveError("文件不存在或路径已变化，请刷新列表")


async def _resolve(user_id: int, record: dict, paths: list[str], fsids: list[str]) -> list[dict]:
    if paths:
        if not 1 <= len(paths) <= 100:
            raise BaiduDriveError("请选择 1 到 100 个文件")
        paths = list(dict.fromkeys(validate_path(p, allow_root=False) for p in paths))
    else:
        ids = _ids(fsids)
        cached = read_private(_records_path(user_id, record["account_id"], "files.json"), {})
        if not isinstance(cached, dict) or any(fsid not in cached for fsid in ids):
            raise BaiduDriveError("请先查询文件列表，或直接提供文件 paths")
        paths = [validate_path(cached[fsid], allow_root=False) for fsid in ids]
    parents: dict[str, list[dict]] = {}
    files = []
    for path in paths:
        parent = str(PurePosixPath(path).parent)
        if parent not in parents:
            parents[parent] = await _directory(user_id, parent)
        row = next((r for r in parents[parent] if r["path"] == path), None)
        if row is None or (not paths or fsids and row["fs_id"] not in fsids):
            raise BaiduDriveError("文件已变化，请刷新列表后重新选择")
        files.append(row)
    return files


async def list_files(user_id: int, directory: str = "/", keyword: str = "", page: int = 1,
                     page_size: int = 100, category: str = "all") -> dict:
    directory = validate_path(directory)
    if not 1 <= page <= 10000 or not 1 <= page_size <= 100 or len(keyword) > 100 or category not in {"all", "folder", *CATEGORIES}:
        raise BaiduDriveError("分页、分类或搜索参数无效")
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        if keyword.strip():
            # The official CLI searches the entire app root, not the active folder.
            size = min(page_size, 50)
            args = ["search", "--page", str(page), "--page-size", str(size)]
            if category == "folder":
                args.append("--dir-only")
            elif category in CATEGORIES:
                args.extend(["--category", str(CATEGORIES[category]), "--no-dir"])
            result = await cli.run(user_id, *args, "--", keyword.strip())
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                raise BaiduDriveError("官方 CLI 未返回有效搜索结果")
            files = [file_view(row) for row in result["items"]]
            total = int(result.get("total", len(files)))
            has_more = total > page * size
            page_size, directory = size, cli.ROOT
        elif category == "all":
            rows = await _list(user_id, directory, (page - 1) * page_size, page_size + 1)
            files, has_more, total = rows[:page_size], len(rows) > page_size, len(rows[:page_size])
        else:
            rows = await _directory(user_id, directory)
            filtered = [r for r in rows if r["isdir"]] if category == "folder" else [r for r in rows if not r["isdir"] and r["category"] == CATEGORIES[category]]
            offset = (page - 1) * page_size
            files, total = filtered[offset:offset + page_size], len(filtered)
            has_more = total > offset + page_size
        _cache(user_id, record, files)
        return {"files": files, "dir": directory, "keyword": keyword.strip(), "page": page,
                "page_size": page_size, "has_more": has_more, "total": total,
                "search_scope": "application" if keyword.strip() else "directory"}


async def list_shares(user_id: int) -> dict:
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        shares = read_private(_records_path(user_id, record["account_id"], "shares.json"), [])
        if not isinstance(shares, list):
            raise BaiduDriveError("项目分享记录格式无效")
        return {"shares": shares, "total": len(shares), "scope": "connection"}


async def create_share(user_id: int, fsids: list[str], title: str, period: int = 7, password: str = "", paths: list[str] | None = None) -> dict:
    if period not in {0, 1, 7, 30} or not title.strip() or len(title) > 100:
        raise BaiduDriveError("分享名称或有效期无效")
    if password:
        raise BaiduDriveError("官方 Skill 自动生成提取码，不支持自定义提取码")
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        files = await _resolve(user_id, record, paths or [], fsids)
        result = await cli.run(user_id, "share", "--period", str(period), "--", *(r["path"] for r in files))
        if not isinstance(result, dict):
            raise BaiduDriveError("百度未返回有效分享结果，请在百度网盘核验，避免重复创建")
        link = cli.trusted_url(result.get("link") or result.get("short_url"))
        if urlsplit(link).hostname != "pan.baidu.com":
            raise BaiduDriveError("百度未返回有效网盘分享链接")
        code = result.get("pwd", "")
        if not isinstance(code, str) or code and not re.fullmatch(r"[a-zA-Z0-9]{4}", code):
            raise BaiduDriveError("百度返回的提取码无效，请在百度网盘核验结果")
        share = {"id": uuid4().hex, "title": title.strip(), "fsids": [r["fs_id"] for r in files],
                 "paths": [r["path"] for r in files], "share_url": link, "passcode": code,
                 "period": period, "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            path = _records_path(user_id, record["account_id"], "shares.json")
            old = read_private(path, [])
            if not isinstance(old, list):
                raise BaiduDriveError("分享记录格式无效")
            write_private(path, [share, *old][:5000])
        except BaiduDriveError:
            return {"share": share, "saved": False, "message": "百度分享已创建，但项目记录保存失败，请立即复制链接，避免重复创建"}
        return {"share": share, "saved": True}


def _safe_result(result: object) -> dict:
    if not isinstance(result, dict) or not result:
        raise BaiduDriveError("官方 CLI 返回的操作结果不完整，请刷新核验")
    data = {key: result[key] for key in ("status", "count", "task_id", "selected_count", "partial", "skipped_count") if isinstance(result.get(key), (str, int, bool))}
    data.update(_links(result))
    for key in ("fsid", "fs_id"):
        if result.get(key) is not None:
            value = str(result[key])
            _ids([value])
            data[key] = value
    for key in ("target_dir", "saved_path"):
        value = result.get(key)
        if isinstance(value, str):
            raw = cli.ROOT + value[len("我的应用数据/bdpan"):] if value.startswith("我的应用数据/bdpan") else value
            validate_path(raw)
            data[key] = value
    if not data:
        raise BaiduDriveError("官方 CLI 未返回可核验的成功标记，请刷新核验结果")
    return data


def _share_url(value: str) -> str:
    link = cli.trusted_url(value)
    if urlsplit(link).hostname != "pan.baidu.com" or not urlsplit(link).path.startswith(("/s/", "/share/")):
        raise BaiduDriveError("请输入百度网盘分享链接")
    return link


async def run_operation(user_id: int, operation: str, payload: dict) -> dict:
    if operation not in OPERATIONS:
        raise BaiduDriveError("官方 Skill 不支持此操作（语义搜索、URL 上传暂不可用）")
    if operation not in {"file-meta", "share-transfer-list"} and payload.get("confirmed") is not True:
        raise BaiduDriveError("请先核对操作对象并确认执行")
    directory = validate_path(payload.get("dir") or "/")
    async with exclusive(user_id):
        record = await auth.require_account(user_id)
        if operation == "file-meta":
            files = await _resolve(user_id, record, payload.get("paths") or [], payload.get("fsids") or [])
            return {"files": files, "message": "文件信息已查询"}
        if operation.startswith("share-transfer"):
            link = _share_url(payload.get("url") or "")
            pwd = payload.get("password") or ""
            if pwd and not re.fullmatch(r"[A-Za-z0-9]{4}", pwd):
                raise BaiduDriveError("提取码须为 4 位字母或数字")
            args = ["transfer"]
            if operation == "share-transfer-select":
                args.extend(["select", "--fsid", ",".join(_ids(payload.get("fsids") or []))])
            elif operation == "share-transfer-list":
                args.extend(["list", "--page", str(payload.get("page", 1)), "--page-size", str(payload.get("page_size", 100))])
                if payload.get("source_dir"):
                    source = payload["source_dir"]
                    if not source.startswith("/") or "\\" in source or any(p in {".", ".."} for p in source.split("/")) or any(ord(c) < 32 for c in source):
                        raise BaiduDriveError("分享来源目录无效")
                    args.extend(["--source-dir", source])
            if operation != "share-transfer-list":
                args.extend(["--dir", validate_path(payload.get("target_dir") or "/")])
            if pwd:
                args.extend(["--pwd", pwd])
            result = await cli.run(user_id, *args, "--", link, timeout=180)
            if operation == "share-transfer-list":
                # A read-only share listing uses paths from that share, not this account.
                if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                    raise BaiduDriveError("官方 CLI 未返回分享文件列表")
                files = [{"fs_id": str(r.get("fs_id", r.get("fsid", ""))), "path": str(r.get("path", "")),
                          "filename": str(r.get("server_filename", r.get("name", ""))), "isdir": bool(r.get("isdir", r.get("is_dir", False))),
                          "size": int(r.get("size", 0))} for r in result["items"] if isinstance(r, dict)]
                for row in files:
                    _ids([row["fs_id"]])
                return {"files": files, "has_more": bool(result.get("has_more")), "total": len(files),
                        "page": result.get("page"), "page_size": result.get("page_size")}
            data = _safe_result(result)
            if isinstance(result, dict) and isinstance(result.get("files"), list) and data.get("status") != "submitted":
                normalized = []
                for file in result["files"]:
                    if not isinstance(file, dict):
                        raise BaiduDriveError("官方 CLI 转存结果不完整，请刷新核验")
                    normalized.append(file_view({**file, "server_filename": file.get("name")}))
                data["files"] = normalized
                _cache(user_id, record, normalized)
            if data.get("status") == "submitted":
                data["message"] = "转存任务已提交，请刷新核验完成状态"
            elif data.get("partial") is True or data.get("skipped_count", 0):
                data.update(status="partial", message=f"转存部分完成，跳过 {data.get('skipped_count', 0)} 项；请核对分享和网盘结果")
            else:
                data["message"] = "官方 CLI 转存已完成"
            return data
        if operation == "upload-text":
            content, name = payload.get("content") or "", validate_name(payload.get("filename") or "文本资料.txt")
            if not 1 <= len(content) <= 20000:
                raise BaiduDriveError("文本长度须为 1 到 20,000 个字符")
            destination = str(PurePosixPath(directory) / name)
            await _no_collision(user_id, directory, name)
            with tempfile.TemporaryDirectory(dir=cli.config_path(user_id).parent) as temp:
                local = Path(temp) / name
                local.write_text(content, encoding="utf-8")
                result = await cli.run(user_id, "upload", "--", str(local), destination, timeout=180)
            return {**_safe_result(result), "message": "文本文件已上传", "path": destination}
        if operation == "create-folder":
            path = str(PurePosixPath(directory) / validate_name(payload.get("name") or ""))
            result = await cli.run(user_id, "mkdir", "--", path)
            return {**_safe_result(result), "message": "文件夹已创建", "path": path}
        files = await _resolve(user_id, record, payload.get("paths") or [], payload.get("fsids") or [])
        target = validate_path(payload.get("target_dir") or "/")
        if operation == "rename" and len(files) != 1:
            raise BaiduDriveError("每次重命名请选择一个文件")
        results = []
        for row in files:
            source = row["path"]
            try:
                if operation == "delete":
                    args = ["rm", "--force", "--", source]
                    destination = source
                elif operation == "rename":
                    name = validate_name(payload.get("name") or "")
                    parent = str(PurePosixPath(source).parent)
                    await _no_collision(user_id, parent, name)
                    args, destination = ["rename", "--", source, name], str(PurePosixPath(parent) / name)
                else:
                    if target == source or target.startswith(source + "/"):
                        raise BaiduDriveError("目标目录不能是文件本身或其子目录")
                    await _no_collision(user_id, target, row["filename"])
                    args = ["cp" if operation == "copy" else "mv", "--", source, target]
                    destination = str(PurePosixPath(target) / row["filename"])
                data = _safe_result(await cli.run(user_id, *args, timeout=180))
                results.append({"path": source, "destination": destination, "status": "success", **data})
            except BaiduDriveError as exc:
                results.append({"path": source, "status": "failed", "message": str(exc)})
        completed = sum(r["status"] in {"success", "ok"} for r in results)
        state = "success" if completed == len(results) else "partial" if completed else "failed"
        return {"status": state, "results": results, "message": f"完成 {completed}/{len(results)} 项操作"}


async def _no_collision(user_id: int, directory: str, name: str) -> None:
    if any(row["filename"] == name for row in await _directory(user_id, directory)):
        raise BaiduDriveError("目标已存在同名文件，请修改名称；项目不会覆盖现有内容")


async def upload_files(user_id: int, files: list[UploadFile], directory: str = "/") -> dict:
    directory = validate_path(directory)
    if not 1 <= len(files) <= 100:
        raise BaiduDriveError("每次上传 1 到 100 个文件")
    maximum = int(os.environ.get("BAIDU_MAX_UPLOAD_BYTES", str(1024**3)))
    async with exclusive(user_id):
        await auth.require_account(user_id)
        results, total = [], 0
        for file in files:
            name = file.filename or ""
            try:
                name = validate_name(name)
                await _no_collision(user_id, directory, name)
                with tempfile.TemporaryDirectory(dir=cli.config_path(user_id).parent) as temp:
                    local = Path(temp) / name
                    with local.open("wb") as stream:
                        while chunk := await file.read(1024**2):
                            total += len(chunk)
                            if total > maximum:
                                raise BaiduDriveError("上传超出项目大小限制")
                            stream.write(chunk)
                    path = str(PurePosixPath(directory) / name)
                    data = _safe_result(await cli.run(user_id, "upload", "--", str(local), path, timeout=900))
                results.append({"filename": name, "status": "success", "path": path, **data})
            except (BaiduDriveError, OSError) as exc:
                results.append({"filename": name, "status": "failed", "message": str(exc) if isinstance(exc, BaiduDriveError) else "本地临时文件处理失败"})
        count = sum(r["status"] in {"success", "ok"} for r in results)
        return {"status": "success" if count == len(results) else "partial" if count else "failed", "results": results,
                "message": f"上传完成 {count}/{len(results)} 个文件"}


async def download(user_id: int, path: str) -> tuple[Path, str, Path]:
    path = validate_path(path, allow_root=False)
    async with exclusive(user_id):
        await auth.require_account(user_id)
        metadata = await _metadata(user_id, path)
        name = validate_name(metadata["filename"])
        maximum = int(os.environ.get("BAIDU_MAX_DOWNLOAD_BYTES", str(256 * 1024**2)))
        if metadata["size"] > maximum:
            raise BaiduDriveError("文件超过项目下载限制，请直接在百度网盘下载")
        temp = Path(tempfile.mkdtemp(dir=cli.config_path(user_id).parent, prefix="download-"))
        local = temp / name
        try:
            await cli.run(user_id, "download", "--", path, str(local), timeout=900, disk_directory=temp, disk_limit=maximum)
            for candidate in temp.rglob("*"):
                if candidate.is_symlink() or not candidate.resolve().is_relative_to(temp.resolve()):
                    raise BaiduDriveError("下载结果包含不安全文件路径")
            if metadata["isdir"]:
                if not local.is_dir():
                    raise BaiduDriveError("官方 CLI 未保存完整目录")
                if sum(p.stat().st_size for p in local.rglob("*") if p.is_file()) > maximum:
                    raise BaiduDriveError("目录超过项目下载限制，请直接在百度网盘下载")
                archive = temp / ("download-" + uuid4().hex + ".zip")
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                    for candidate in local.rglob("*"):
                        output.write(candidate, candidate.relative_to(temp))
                return archive, name + ".zip", temp
            if not local.is_file() or local.stat().st_size > maximum:
                raise BaiduDriveError("官方 CLI 未保存完整文件或文件超出限制")
            return local, name, temp
        except BaseException:
            shutil.rmtree(temp, ignore_errors=True)
            raise
