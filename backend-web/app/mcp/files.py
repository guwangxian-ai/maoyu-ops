"""Private, expiring file handles for MCP multipart uploads and downloads."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4
from weakref import WeakValueDictionary

import anyio

MAX_BYTES = 256 * 1024 * 1024
OWNER_QUOTA = 1024 * 1024 * 1024
TTL_SECONDS = 86400
_owner_writes = WeakValueDictionary()


def root() -> Path:
    return Path(os.environ.get("MCP_FILE_DIR", str(Path(__file__).resolve().parents[3] / "data/mcp-files")))


def owner_dir(owner_id: int) -> Path:
    directory = root() / f"user-{int(owner_id)}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("文件存储路径无效")
    os.chmod(directory, 0o700)
    return directory


def lookup(owner_id: int, identifier: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("文件编号无效")
    folder = owner_dir(owner_id) / identifier
    metadata, blob = folder / "meta.json", folder / "blob"
    if folder.is_symlink() or metadata.is_symlink() or blob.is_symlink():
        raise ValueError("文件不可访问")
    try:
        record = json.loads(metadata.read_text())
        if record["expires_at"] <= time.time() or not blob.is_file():
            raise ValueError("文件已过期，请重新上传或下载")
        return record, blob
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("文件不存在或不属于当前用户") from exc


def describe(record: dict) -> dict:
    return {**record, "download_path": f"/api/v1/mcp/files/{record['upload_id']}/content",
            "authentication": "Authorization: Bearer <MCP token or website JWT>"}


async def store_stream(owner_id: int, filename: str, content_type: str, chunks) -> dict:
    # Serialize reservations for concurrent uploads in this backend process.
    # Weak references release idle locks without retaining every past user.
    key = (str(root().resolve()), int(owner_id))
    lock = _owner_writes.setdefault(key, asyncio.Lock())
    async with lock:
        return await _store_stream(owner_id, filename, content_type, chunks)


async def _store_stream(owner_id: int, filename: str, content_type: str, chunks) -> dict:
    directory = owner_dir(owner_id)
    used = 0
    for item in directory.iterdir():
        if not item.is_dir() or item.is_symlink() or not re.fullmatch(r"[a-f0-9]{32}", item.name):
            continue
        try:
            meta = json.loads((item / "meta.json").read_text())
            if meta["expires_at"] <= time.time():
                shutil.rmtree(item)
            else:
                used += (item / "blob").stat().st_size
        except (OSError, ValueError, KeyError):
            continue
    identifier = uuid4().hex
    folder = directory / identifier
    folder.mkdir(mode=0o700)
    size, digest = 0, hashlib.sha256()
    filename = Path(filename.replace("\\", "/")).name[:200] or "file.bin"
    try:
        fd = os.open(folder / "blob", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        async with await anyio.open_file(fd, "wb") as target:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES or used + size > OWNER_QUOTA:
                    raise ValueError("超过 MCP 单文件 256 MiB 或用户暂存 1 GiB 限制")
                digest.update(chunk)
                await target.write(chunk)
        record = {"upload_id": identifier, "filename": filename, "content_type": content_type or "application/octet-stream",
                  "size": size, "sha256": digest.hexdigest(), "expires_at": int(time.time()) + TTL_SECONDS}
        fd = os.open(folder / "meta.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, ensure_ascii=False)
        return describe(record)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


async def store_bytes(owner_id: int, filename: str, data: bytes, content_type: str = "application/octet-stream"):
    async def chunks():
        yield data
    return await store_stream(owner_id, filename, content_type, chunks())


async def read_chunk(owner_id: int, upload_id: str, offset: int = 0, limit: int = 65536):
    record, blob = lookup(owner_id, upload_id)
    if offset < 0 or offset > record["size"] or not 1 <= limit <= 131072:
        raise ValueError("offset/limit 超出范围")
    async with await anyio.open_file(blob, "rb") as source:
        await source.seek(offset)
        data = await source.read(limit)
    end = offset + len(data)
    return {**describe(record), "offset": offset, "content_base64": base64.b64encode(data).decode(),
            "next_offset": end if end < record["size"] else None}
