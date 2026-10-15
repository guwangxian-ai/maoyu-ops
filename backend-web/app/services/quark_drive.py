"""File browsing and share records backed by the official Quark CLI."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.services.quark_drive_auth import (
    QuarkAuthError,
    _has_account,
    _account_key,
    _legacy_account_key,
    _record_legacy_account,
    _run_cli,
    _read_lock,
    _user_dir,
    _user_locks,
)


FID_PATTERN = re.compile(r"^[A-Za-z0-9._~|+/=-]{1,2048}$")
FILE_FIELDS = ("fid", "parent_fid", "filename", "size", "file_type", "category", "updated_at", "includeItems",
               "content_hash", "created_at", "full_path", "format_type", "duration", "video_width", "video_height",
               "video_max_resolution", "image_info", "l_shot_at", "series_info_v2", "source_display", "upload_device",
               "shoot_device", "shoot_address", "obj_category", "big_thumbnail", "check_link")


def _validate_fid(fid: str) -> str:
    if not FID_PATTERN.fullmatch(fid) or fid.startswith("-"):
        raise QuarkAuthError("文件或目录标识无效，请刷新文件列表")
    return fid


def _require_account(user_id: int) -> None:
    if not _has_account(user_id):
        raise QuarkAuthError("请先授权夸克网盘账号")


def _check_result(result: dict, fallback: str) -> dict:
    if result.get("code") != 0:
        if result.get("code") in (-103, -1408):
            raise QuarkAuthError("夸克授权已失效，请重新授权")
        raise QuarkAuthError(str(result.get("msg") or fallback)[:240])
    data = result.get("data")
    if not isinstance(data, dict):
        raise QuarkAuthError("夸克接口返回的数据格式不正确")
    return data


def _read_file_artifact(user_id: int, artifact: object) -> list[dict]:
    if not isinstance(artifact, dict) or not isinstance(artifact.get("file_path"), str):
        raise QuarkAuthError("夸克没有返回完整文件列表，请重试")
    path = Path(artifact["file_path"]).resolve()
    if not path.is_relative_to(_user_dir(user_id).resolve()):
        raise QuarkAuthError("夸克文件列表位置无效")
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise QuarkAuthError("此目录文件过多，请在夸克中拆分目录后重试")
        items = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get("fid"), str):
                    raise QuarkAuthError("夸克文件列表格式不正确")
                items.append({key: item[key] for key in FILE_FIELDS if key in item})
        if artifact.get("count") != len(items):
            raise QuarkAuthError("夸克文件列表不完整，请重试")
        return items
    except (OSError, ValueError) as exc:
        raise QuarkAuthError("读取夸克文件列表失败，请重试") from exc
    finally:
        # The CLI writes a new snapshot for every query; keep no stale copies.
        path.unlink(missing_ok=True)


async def list_files(user_id: int, parent_fid: str = "0", keyword: str = "", search_type: str = "mix") -> dict:
    _validate_fid(parent_fid)
    keyword = keyword.strip()
    if len(keyword) > 50:
        raise QuarkAuthError("搜索关键词最多 50 个字符")
    if search_type not in {"mix", "video", "album", "doc", "audio", "dir", "package", "other", "app"}:
        raise QuarkAuthError("搜索类型无效")
    async with _read_lock(user_id):
        _require_account(user_id)
        if keyword:
            args = ["--keyword", keyword, "--search-type", search_type]
            # Search uses an omitted directory for the account root. Passing
            # browse's virtual "0" produces an empty scoped result upstream.
            if parent_fid != "0":
                args += ["--parent-fid", parent_fid]
            result = await _run_cli(user_id, "search", *args, timeout=75)
            if parent_fid != "0" and result.get("code") != 0 and result.get("msg") == "文件找不到":
                raise QuarkAuthError("夸克当前文件夹搜索失败：文件找不到。可返回「全部文件」进行全盘搜索。")
        else:
            result = await _run_cli(user_id, "browse", "--parent-fid", parent_fid,
                                    "--page-size", "100", "--all", timeout=75)
        _check_result(result, "读取夸克文件列表失败")
        files = _read_file_artifact(user_id, result.get("_artifact"))
        if not keyword and search_type != "mix":
            category = {"dir": 0, "video": 1, "audio": 2, "album": 3, "doc": 4, "other": 6, "package": 7, "app": 8}[search_type]
            files = [row for row in files if row.get("category") == category]
    return {"files": files, "total": len(files), "parent_fid": parent_fid, "keyword": keyword, "search_type": search_type}


def _share_path(user_id: int) -> Path:
    key = _account_key(user_id)
    if key is None:
        return _user_dir(user_id) / "shares.json"
    _record_legacy_account(user_id)
    if key == _legacy_account_key(user_id):
        return _user_dir(user_id) / "shares.json"
    return _user_dir(user_id) / "accounts" / key / "shares.json"


def _read_shares(user_id: int) -> list[dict]:
    path = _share_path(user_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError("Invalid share records")
        return records
    except (OSError, ValueError) as exc:
        raise QuarkAuthError("读取分享记录失败，请检查本地数据目录") from exc


def _write_shares(user_id: int, records: list[dict]) -> None:
    path = _share_path(user_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f"shares.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            json.dump(records, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def list_shares(user_id: int) -> dict:
    # Atomic replacement makes a snapshot safe to read during a long transfer.
    records = _read_shares(user_id)
    return {"shares": records, "total": len(records)}


async def create_share(user_id: int, fids: list[str], title: str,
                       url_type: int, expired_type: int) -> dict:
    if not 1 <= len(fids) <= 100 or len(set(fids)) != len(fids):
        raise QuarkAuthError("请选择 1 到 100 个不同的文件或文件夹")
    for fid in fids:
        _validate_fid(fid)
    title = title.strip()
    if not title or len(title) > 100 or any(ord(c) < 32 for c in title):
        raise QuarkAuthError("分享标题需为 1 到 100 个字符")
    if url_type not in (1, 2) or expired_type not in range(1, 8):
        raise QuarkAuthError("分享类型或有效期无效")
    lock = _user_locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        raise QuarkAuthError("当前夸克任务正在执行，请完成后再创建分享")
    async with lock:
        _require_account(user_id)
        # Validate local persistence before creating anything on Quark.
        records = _read_shares(user_id)
        result = await _run_cli(user_id, "share", *fids, "--title", title,
                                "--url-type", str(url_type), "--expired-type", str(expired_type), timeout=60)
        data = _check_result(result, "创建夸克分享失败")
        share_url = data.get("share_url")
        parsed = urlsplit(share_url) if isinstance(share_url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.hostname != "pan.quark.cn" or not parsed.path.startswith("/s/"):
            raise QuarkAuthError("夸克未返回有效的分享链接，请先在夸克中检查分享结果")
        record = {"id": uuid4().hex, "account_id": _account_key(user_id), "title": title, "fids": fids,
                  "share_url": share_url, "passcode": str(data.get("passcode") or ""),
                  "url_type": url_type, "expired_type": expired_type,
                  "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            _write_shares(user_id, [record, *records])
        except OSError:
            # Creation has succeeded remotely: return the URL even if saving
            # failed so the user does not lose it or accidentally create twice.
            return {"share": record, "saved": False,
                    "message": "分享已创建，但本地记录保存失败，请立即复制链接"}
    return {"share": record, "saved": True}
