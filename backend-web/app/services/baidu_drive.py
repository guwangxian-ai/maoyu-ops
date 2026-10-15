"""Baidu file browsing, MCP operations, uploads and account-bound shares.

Upload flow follows the MIT-licensed baidu-netdisk/mcp example: precreate,
4 MiB blocks, superfile2, create. Existing httpx/UploadFile are reused instead
of copying the generated SDK or starting a second local MCP server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import string
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import UploadFile

from app.services.baidu_drive_auth import (BaiduDriveError, account_token, exclusive, provider_json,
                                          read_private, user_dir, write_private, _account)
from app.services.baidu_drive_mcp import call_tool


FILE_API = "https://pan.baidu.com/rest/2.0/xpan/file"
CHUNK_SIZE = 4 * 1024 * 1024
CATEGORIES = {"video": 1, "audio": 2, "image": 3, "doc": 4, "app": 5, "other": 6}
OPERATIONS = ("create-folder", "copy", "move", "rename", "delete", "file-meta",
              "semantic-search", "upload-url", "upload-text")


def validate_path(path: str, *, allow_root: bool = True) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "\\" in path or len(path.encode("utf-8")) > 4096:
        raise BaiduDriveError("网盘路径必须是以 / 开头的绝对路径")
    value = path.rstrip("/") or "/"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BaiduDriveError("网盘路径不能包含控制字符")
    if value != "/" and any(part in {"", ".", ".."} for part in value[1:].split("/")):
        raise BaiduDriveError("网盘路径不能包含空目录、. 或 ..")
    if not allow_root and value == "/":
        raise BaiduDriveError("不能对网盘根目录执行此操作")
    return value


def validate_name(name: str) -> str:
    if not name or name in {".", ".."} or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name) or len(name.encode("utf-8")) > 255:
        raise BaiduDriveError("文件名称无效，不能含路径分隔符或特殊字符，且不能超过 255 字节")
    return name


def validate_fsids(fsids: list[str], maximum: int = 100) -> list[str]:
    if not 1 <= len(fsids) <= maximum or any(not re.fullmatch(r"[0-9]{1,20}", value) or int(value) == 0 for value in fsids):
        raise BaiduDriveError(f"请选择 1 到 {maximum} 个有效文件")
    return list(dict.fromkeys(fsids))


def file_view(row: dict) -> dict:
    fsid = str(row.get("fs_id", row.get("fsid", "")))
    path = row.get("path", row.get("remote_path", ""))
    if not isinstance(path, str) or not re.fullmatch(r"[0-9]{1,20}", fsid):
        raise BaiduDriveError("百度未返回完整文件标识或路径，请刷新后重试")
    validate_path(path, allow_root=False)
    try:
        timestamp = float(row.get("server_mtime", row.get("mtime", row.get("updated_at", 0))) or 0)
        result = {"fs_id": fsid, "path": path, "filename": str(row.get("server_filename") or row.get("filename") or PurePosixPath(path).name),
                  "size": max(0, int(row.get("size") or 0)), "isdir": row.get("isdir") in {True, 1, "1"},
                  "category": int(row.get("category") or 0), "updated_at": int(timestamp * 1000 if timestamp < 10**12 else timestamp)}
    except (ValueError, TypeError) as exc:
        raise BaiduDriveError("百度返回的文件属性格式无效") from exc
    for key in ("md5", "abstract", "content"):
        if isinstance(row.get(key), str):
            result[key] = row[key]
    return result


def _rows(data: dict) -> list[dict]:
    values = data.get("list", data.get("file_list", data.get("files")))
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise BaiduDriveError("百度未返回完整文件列表，请重新查询")
    return values


def _has_more(data: dict, count: int, page_size: int) -> bool:
    more = data.get("has_more", data.get("hasmore"))
    return more in {True, 1, "1"} if more is not None else count >= page_size


async def list_files(user_id: int, directory: str = "/", keyword: str = "", page: int = 1,
                     page_size: int = 100, category: str = "all") -> dict:
    directory = validate_path(directory)
    if not 1 <= page <= 10000 or not 1 <= page_size <= 100 or len(keyword) > 100:
        raise BaiduDriveError("分页或搜索参数无效")
    if category not in {"all", "folder", *CATEGORIES}:
        raise BaiduDriveError("文件分类无效")
    keyword = keyword.strip()
    async with exclusive(user_id):
        _, token = await account_token(user_id)
        async def fetch(query_page: int, size: int) -> dict:
            parameters = {"access_token": token, "dir": directory}
            if keyword:
                parameters.update({"method": "search", "key": keyword, "page": query_page, "num": size, "recursion": 1})
            else:
                parameters.update({"method": "list", "start": (query_page - 1) * size, "limit": size, "order": "time", "desc": 1})
                if category == "folder":
                    parameters["folder"] = 1
            return await provider_json(FILE_API, params=parameters)
        if category in CATEGORIES or (category == "folder" and keyword):
            # Filter the entire directory/search result before pagination, so a
            # category never silently disappears because of a mixed first page.
            rows = []
            for index in range(1, 21):
                data = await fetch(index, 500)
                batch = _rows(data)
                rows.extend(batch)
                if not _has_more(data, len(batch), 500):
                    break
            else:
                raise BaiduDriveError("此目录超过 10,000 项，请进入子目录或缩小关键词后筛选")
            files = [file_view(row) for row in rows]
            files = [row for row in files if row["isdir"]] if category == "folder" else [row for row in files if not row["isdir"] and row["category"] == CATEGORIES[category]]
            start = (page - 1) * page_size
            has_more = len(files) > start + page_size
            files = files[start:start + page_size]
        else:
            data = await fetch(page, page_size)
            rows = _rows(data)
            files = [file_view(row) for row in rows]
            has_more = _has_more(data, len(rows), page_size)
    return {"files": files, "dir": directory, "keyword": keyword, "page": page,
            "page_size": page_size, "has_more": has_more, "total": len(files)}


def _share_path(user_id: int, account_id: str | None = None):
    if not account_id:
        account = _account(user_id)
        if not account:
            raise BaiduDriveError("请先登录授权百度网盘")
        account_id = account["account_id"]
    if not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise BaiduDriveError("百度网盘账号标识无效")
    return user_dir(user_id) / "accounts" / account_id / "shares.json"


async def list_shares(user_id: int) -> dict:
    records = read_private(_share_path(user_id), [])
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise BaiduDriveError("百度分享记录格式无效")
    return {"shares": records, "total": len(records), "scope": "project"}


async def create_share(user_id: int, fsids: list[str], title: str, period: int = 7, password: str = "") -> dict:
    fsids = validate_fsids(fsids)
    if period not in {0, 1, 7, 30, 365}:
        raise BaiduDriveError("分享有效期无效")
    if not title.strip() or len(title) > 100:
        raise BaiduDriveError("请填写 1 到 100 个字符的分享名称")
    password = password or "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    if not re.fullmatch(r"[a-z0-9]{4}", password):
        raise BaiduDriveError("提取码须为 4 位小写字母或数字")
    async with exclusive(user_id):
        account, token = await account_token(user_id)
        data = await call_tool(token, "file_sharelink_set", {"fsid_list": fsids, "period": period, "pwd": password})
        link = data.get("link") or data.get("short_url") or data.get("share_url")
        try:
            url = urlsplit(link) if isinstance(link, str) else None
            valid = url and url.scheme == "https" and url.hostname == "pan.baidu.com" and url.port in {None, 443} and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid:
            raise BaiduDriveError("百度未返回有效分享链接，请在百度网盘核验是否已创建，避免重复分享")
        code = str(data.get("pwd") or data.get("passcode") or password)
        if not re.fullmatch(r"[a-zA-Z0-9]{4}", code):
            raise BaiduDriveError("百度返回的提取码格式无效，请在网盘核验分享结果")
        share = {"id": uuid4().hex, "title": title.strip(), "fsids": fsids, "share_url": link,
                 "passcode": code, "period": period, "created_at": datetime.now(timezone.utc).isoformat()}
        path = _share_path(user_id, account["account_id"])
        try:
            records = read_private(path, [])
            if not isinstance(records, list):
                raise BaiduDriveError("分享记录格式无效")
            write_private(path, [share, *records][:5000])
        except BaiduDriveError:
            return {"share": share, "saved": False, "message": "百度分享已创建，但本项目记录保存失败，请立即复制链接，避免重复创建"}
        return {"share": share, "saved": True}


async def run_operation(user_id: int, operation: str, payload: dict) -> dict:
    if operation not in OPERATIONS:
        raise BaiduDriveError("不支持此百度网盘操作")
    if operation not in {"file-meta", "semantic-search"} and payload.get("confirmed") is not True:
        raise BaiduDriveError("请先核对操作对象并确认执行")
    directory = validate_path(payload.get("dir") or "/")
    paths = payload.get("paths") or []
    if operation in {"copy", "move", "rename", "delete"}:
        if not 1 <= len(paths) <= 100:
            raise BaiduDriveError("请选择 1 到 100 个文件")
        paths = list(dict.fromkeys(validate_path(path, allow_root=False) for path in paths))
    if operation == "create-folder":
        # Unlike JSON filelist fields, make_dir explicitly requires an encoded path.
        path = str(PurePosixPath(directory) / validate_name(payload.get("name") or ""))
        tool, values = "make_dir", {"path": quote(path, safe=""), "rtype": 0}
    elif operation in {"copy", "move"}:
        target = validate_path(payload.get("target_dir") or "")
        if any(target == path or target.startswith(path + "/") for path in paths):
            raise BaiduDriveError("目标目录不能是文件本身或其子目录")
        tool, values = f"file_{operation}", {"async": 0, "ondup": "fail", "filelist": [
            {"path": path, "dest": target, "newname": PurePosixPath(path).name} for path in paths]}
    elif operation == "rename":
        if len(paths) != 1:
            raise BaiduDriveError("每次重命名请选择一个文件或文件夹")
        tool, values = "file_rename", {"async": 0, "ondup": "fail", "filelist": [{"path": paths[0], "newname": validate_name(payload.get("name") or "")} ]}
    elif operation == "delete":
        tool, values = "file_del", {"async": 0, "ondup": "fail", "filelist": paths}
    elif operation == "file-meta":
        tool, values = "file_meta", {"fsids": validate_fsids(payload.get("fsids") or [], 10)}
    elif operation == "semantic-search":
        query = (payload.get("query") or "").strip()
        if not 1 <= len(query) <= 1000:
            raise BaiduDriveError("请填写 1 到 1,000 个字符的搜索描述")
        tool, values = "file_semantics_search", {"dir": directory, "query": query}
    elif operation == "upload-url":
        source = payload.get("url") or ""
        url = urlsplit(source)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise BaiduDriveError("请输入有效的 HTTP / HTTPS 下载地址")
        tool, values = "file_upload_by_url", {"dir": directory, "url": source}
        if payload.get("filename"):
            values["filename"] = validate_name(payload["filename"])
    else:
        content = payload.get("content") or ""
        if not 1 <= len(content) <= 20000:
            raise BaiduDriveError("文本长度须为 1 到 20,000 个字符")
        tool, values = "file_upload_by_text", {"dir": directory, "content": content, "filename": validate_name(payload.get("filename") or "文本资料.docx")}
    async with exclusive(user_id):
        _, token = await account_token(user_id)
        data = await call_tool(token, tool, values)
        if operation in {"file-meta", "semantic-search"}:
            return {"files": [file_view(row) for row in _rows(data)], "message": "查询完成"}
        info = data.get("info")
        failed = [item for item in info if str(item.get("errno", 0)) != "0"] if isinstance(info, list) else []
        public = {key: data[key] for key in ("fsid", "fs_id", "path", "isdir", "size", "taskid", "errno") if key in data}
        if failed:
            return {"data": {**public, "status": "partial", "failed": [{"path": item.get("path"), "errno": item.get("errno")} for item in failed]},
                    "message": f"部分操作未完成（{len(failed)} 项），请刷新文件列表核验；未自动重试"}
        if data.get("taskid"):
            return {"data": {**public, "status": "submitted"}, "message": "百度已接收任务，请稍后刷新文件列表核验结果"}
        return {"data": public, "message": "操作已完成"}


async def _upload_file(token: str, upload: UploadFile, remote_path: str, size: int) -> dict:
    block_hashes = []
    await upload.seek(0)
    while block := await upload.read(CHUNK_SIZE):
        block_hashes.append(hashlib.md5(block).hexdigest())
    if not block_hashes:
        block_hashes = [hashlib.md5(b"").hexdigest()]
    block_list = json.dumps(block_hashes, separators=(",", ":"))
    precreate = await provider_json(FILE_API, params={"method": "precreate", "access_token": token},
        data={"path": remote_path, "size": str(size), "isdir": "0", "autoinit": "1", "rtype": "0", "block_list": block_list})
    upload_id = precreate.get("uploadid")
    if not isinstance(upload_id, str) or not upload_id:
        raise BaiduDriveError("百度没有返回有效上传会话，请核验文件是否存在或目录是否有权限")
    missing = precreate.get("block_list", list(range(len(block_hashes))))
    if not isinstance(missing, list):
        raise BaiduDriveError("百度上传分片信息无效")
    for index in missing:
        if not str(index).isdigit() or not 0 <= int(index) < len(block_hashes):
            raise BaiduDriveError("百度返回了无效分片编号")
        index = int(index)
        await upload.seek(index * CHUNK_SIZE)
        block = await upload.read(CHUNK_SIZE)
        result = await provider_json("https://d.pcs.baidu.com/rest/2.0/pcs/superfile2", timeout=180,
            params={"method": "upload", "access_token": token, "type": "tmpfile", "path": remote_path, "uploadid": upload_id, "partseq": index},
            files={"file": (upload.filename or "file", block, "application/octet-stream")})
        if result.get("md5") != block_hashes[index]:
            raise BaiduDriveError("百度上传分片校验失败，文件尚未完成创建")
    created = await provider_json(FILE_API, params={"method": "create", "access_token": token},
        data={"path": remote_path, "size": str(size), "isdir": "0", "rtype": "0", "uploadid": upload_id, "block_list": block_list})
    return file_view(created)


async def upload_files(user_id: int, uploads: list[UploadFile], directory: str) -> dict:
    directory = validate_path(directory)
    if not 1 <= len(uploads) <= 100:
        raise BaiduDriveError("每次请选择 1 到 100 个文件")
    maximum = int(os.environ.get("BAIDU_MAX_UPLOAD_BYTES", str(1024**3)))
    total, prepared, names = 0, [], set()
    # Validate the whole request before the first remote write.
    for upload in uploads:
        name = validate_name(upload.filename or "")
        if name in names:
            raise BaiduDriveError("本次上传包含同名文件，请分开上传")
        names.add(name)
        if upload.size is None:
            raise BaiduDriveError("无法确认上传文件大小，请重新选择文件")
        total += upload.size
        if total > maximum:
            raise BaiduDriveError("本次上传总大小超过服务端限制，默认最多 1 GiB")
        prepared.append((upload, str(PurePosixPath(directory) / name), upload.size))
    files, failed = [], []
    async with exclusive(user_id):
        _, token = await account_token(user_id)
        for upload, path, size in prepared:
            try:
                files.append(await _upload_file(token, upload, path, size))
            except BaiduDriveError as exc:
                failed.append({"filename": upload.filename, "message": str(exc)})
    status = "partial" if failed and files else "failed" if failed else "succeeded"
    return {"files": files, "failed": failed, "status": status,
            "message": f"已上传 {len(files)} 个文件" + (f"，{len(failed)} 个未完成，请核验结果后重试失败项" if failed else "")}
