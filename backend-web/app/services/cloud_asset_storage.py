"""Private immutable staging and bounded public HTTP imports; no cloud credentials."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import socket
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import aiohttp
from aiohttp.resolver import DefaultResolver

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_FILES = 100


class AssetError(Exception):
    pass


def root() -> Path:
    base = Path(os.environ.get("CLOUD_ASSET_DIR") or str(PROJECT_ROOT / "data/cloud-assets")).resolve()
    for public in (PROJECT_ROOT / "backend-web/static", PROJECT_ROOT / "frontend/public", PROJECT_ROOT / "frontend/dist"):
        if base.is_relative_to(public):
            raise AssetError("资料暂存目录不能位于公开静态目录")
    return base


def maximum() -> int:
    return int(os.environ.get("CLOUD_ASSET_MAX_BYTES", str(256 * 1024**2)))


def directory(owner: int, asset_id: str, version: int) -> Path:
    if owner <= 0 or not re.fullmatch(r"[a-f0-9]{32}", asset_id) or version < 1:
        raise AssetError("资料标识无效")
    base = root() / f"user-{owner}" / asset_id / f"v{version}"
    for path in (base.parent.parent, base.parent, base):
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise AssetError("资料暂存目录包含不安全路径")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (root(), base.parent.parent, base.parent, base):
        path.chmod(0o700)
    return base


def filename(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', value) or len(value.encode()) > 200:
        raise AssetError("文件名无效，请使用不含路径的文件名（最多 200 字节）")
    return value


def describe(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root()):
        raise AssetError("资料文件位置无效")
    digest, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk)
            md5.update(chunk)
    path.chmod(0o600)
    return {"name": filename(path.name), "size": path.stat().st_size,
            "sha256": digest.hexdigest(), "md5": md5.hexdigest()}


async def stage_uploads(owner: int, asset_id: str, version: int, files) -> list[dict]:
    if not 1 <= len(files) <= MAX_FILES:
        raise AssetError("请选择 1 到 100 个文件")
    target = directory(owner, asset_id, version)
    names = [filename(file.filename or "") for file in files]
    if len({name.casefold() for name in names}) != len(names):
        raise AssetError("同一资料中不能包含同名文件")
    total, manifest = 0, []
    for name, upload in zip(names, files):
        path = target / name
        with path.open("xb") as stream:
            path.chmod(0o600)
            while chunk := await upload.read(1024**2):
                total += len(chunk)
                if total > maximum():
                    raise AssetError("资料大小超过本地同步限制")
                stream.write(chunk)
        manifest.append(await asyncio.to_thread(describe, path))
    return manifest


def public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise AssetError("下载直链格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or port not in {None, 80, 443} or parsed.fragment:
        raise AssetError("仅支持不含登录信息的公开 HTTP/HTTPS 下载直链")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
            raise AssetError("不能导入本机或内网地址")
    else:
        if not address.is_global:
            raise AssetError("不能导入本机或内网地址")
    return value


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await super().resolve(host, port, family)
        if any(ipaddress.ip_address(row["host"]) in ipaddress.ip_network("198.18.0.0/15") for row in records if ":" not in row["host"]):
            raise AssetError("当前代理 DNS 返回保留地址，直链安全校验已拒绝；请使用正常公网 DNS，或改用网盘文件/分享导入")
        if not records or any(not ipaddress.ip_address(row["host"]).is_global for row in records):
            raise AssetError("下载地址解析到了非公网网络，已拒绝")
        return records


async def download_url(url: str, destination: Path) -> dict:
    public_url(url)
    filename(destination.name)
    resolver = PublicResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                timeout=aiohttp.ClientTimeout(total=900, connect=20), auto_decompress=False) as client:
            current = url
            for _ in range(6):
                public_url(current)
                async with client.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise AssetError("下载直链返回了无效跳转")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise AssetError(f"下载直链返回 HTTP {response.status}")
                    if "text/html" in response.headers.get("Content-Type", "").lower():
                        raise AssetError("该链接返回网页，请使用文件直链或网盘分享导入")
                    if response.content_length is not None and response.content_length > maximum():
                        raise AssetError("下载文件超过同步大小限制")
                    count = 0
                    with destination.open("xb") as output:
                        destination.chmod(0o600)
                        async for chunk in response.content.iter_chunked(1024**2):
                            count += len(chunk)
                            if count > maximum():
                                raise AssetError("下载文件超过同步大小限制")
                            output.write(chunk)
                    if response.content_length is not None and count != response.content_length:
                        raise AssetError("下载内容不完整")
                    return await asyncio.to_thread(describe, destination)
            raise AssetError("下载直链跳转过多")
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise AssetError("下载直链失败或超时，请检查地址及下载权限") from exc
    finally:
        await resolver.close()


def analyze_files(base: Path, manifest: list[dict]) -> dict:
    """Expose real extracted text for AI clients, without inventing a summary."""
    results = []
    for row in manifest:
        path = base / filename(row["name"])
        if describe(path)["sha256"] != row["sha256"]:
            raise AssetError("暂存资料校验失败，请重新上传")
        result = {**row, "text": "", "extraction": "metadata-only"}
        if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".log"}:
            with path.open("rb") as stream:
                raw = stream.read(100001)
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    result.update(text=raw[:100000].decode(encoding)[:20000], extraction="text", truncated=len(raw) > 20000)
                    break
                except UnicodeDecodeError:
                    continue
        elif path.suffix.lower() == ".docx":
            try:
                with zipfile.ZipFile(path) as archive:
                    info = archive.getinfo("word/document.xml")
                    if info.file_size > 8 * 1024**2:
                        raise AssetError("Word 文档文本超过分析限制")
                    tree = ElementTree.fromstring(archive.read(info))
                    result.update(text="\n".join(node.text or "" for node in tree.iter() if node.tag.endswith("}t"))[:20000], extraction="docx")
            except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
                result["extraction"] = "unreadable-document"
        results.append(result)
    return {"method": "file-metadata-and-text-extraction", "ai_generated": False,
            "file_count": len(results), "total_bytes": sum(row["size"] for row in manifest),
            "files": results, "message": "已提取文件原文与基本信息，可供 AI 继续分析。"}
