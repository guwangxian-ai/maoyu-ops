"""Call the existing HTTP handlers through a private, owner-scoped ASGI app.

No public credential header or global dependency override can impersonate the
owner. Only this in-process app receives its principal in a private ASGI scope.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from copy import copy
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.routing import request_response
import httpx

from app.api import deps
from app.mcp import files
from app.mcp.ownership import validate_references

SECRET_KEYS = {"cookie", "cookies", "cookies_str", "cookie_str", "password", "password_hash",
               "secret_key", "access_token", "refresh_token", "token", "authorization", "client_secret",
               "api_key", "api_config", "text_api_key", "image_api_key", "login_password", "proxy_pass",
               "bduss", "stoken", "geetest_validate", "geetest_seccode"}


def redact(value, hidden_keys=frozenset()):
    if isinstance(value, list):
        return [redact(item, hidden_keys) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, hidden_keys) for key, item in value.items()
                if key.lower().replace("-", "_") not in SECRET_KEYS | hidden_keys
                and not key.lower().endswith(("_token", "_api_key", "_secret", "_password"))}
    return value


def check_media_references(value, media=False):
    """MCP cannot use publishing as an arbitrary server-file reader or URL fetcher."""
    if isinstance(value, dict):
        for key, item in value.items():
            is_media = key in {"images", "image", "image_url", "image_urls", "videos", "video_url", "cover", "cover_url"}
            check_media_references(item, is_media or (media and key in {"path", "url", "poster", "cover"}))
    elif isinstance(value, list):
        for item in value:
            check_media_references(item, media)
    elif media and isinstance(value, str) and value:
        from app.core.paths import STATIC_ROOT
        parsed = urlsplit(value)
        if parsed.scheme:
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or not (host == "alicdn.com" or host.endswith(".alicdn.com") or host.endswith(".goofish.com"))):
                raise ValueError("远程商品媒体仅接受闲鱼/阿里 CDN；其他媒体请通过上传工具提交")
        else:
            normalized = value.replace("\\", "/")
            if not normalized.startswith(("/static/uploads/", "static/uploads/")) or "%" in normalized:
                raise ValueError("商品媒体须使用上传接口返回的 /static/uploads/ 路径")
            target = (STATIC_ROOT / normalized.lstrip("/").removeprefix("static/")).resolve()
            if not target.is_relative_to((STATIC_ROOT / "uploads").resolve()):
                raise ValueError("商品媒体路径越界")


class BusinessBridge:
    def __init__(self, catalog):
        self.app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        self.background: set[asyncio.Task] = set()

        async def principal(request: Request):
            return request.scope["mcp_owner"]

        self.app.dependency_overrides[deps.get_current_user] = principal
        self.app.dependency_overrides[deps.get_current_active_user] = principal
        seen = set()
        for spec in catalog.values():
            if id(spec.route) in seen:
                continue
            seen.add(id(spec.route))
            route = copy(spec.route)
            route.dependency_overrides_provider = self.app
            original = route.get_route_handler()

            async def handler(request: Request, original=original):
                response = await original(request)
                if isinstance(response, (FileResponse, StreamingResponse)):
                    async def chunks():
                        if isinstance(response, FileResponse):
                            async with await anyio.open_file(response.path, "rb") as source:
                                while chunk := await source.read(65536):
                                    yield chunk
                        else:
                            async for chunk in response.body_iterator:
                                yield chunk.encode() if isinstance(chunk, str) else chunk
                    try:
                        result = await files.store_stream(request.scope["mcp_owner"].id,
                            getattr(response, "filename", None) or "download.bin",
                            response.media_type or "application/octet-stream", chunks())
                    finally:
                        if response.background:
                            await response.background()
                    return JSONResponse({"success": True, "data": {"file": result}})
                if response.background:
                    # Native BackgroundTasks must keep running after the MCP response.
                    # Retain tasks and drain them during the parent application's shutdown.
                    task = asyncio.create_task(response.background())
                    self.background.add(task)
                    task.add_done_callback(self._finished)
                    response.background = None
                return response

            route.app = request_response(handler)
            self.app.router.routes.append(route)

    def _finished(self, task):
        self.background.discard(task)
        if not task.cancelled() and task.exception():
            from loguru import logger
            logger.error("MCP business background task failed: {}", type(task.exception()).__name__)

    async def close(self):
        if self.background:
            done, pending = await asyncio.wait(self.background, timeout=20)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def invoke(self, spec, arguments: dict, user):
        await validate_references(spec, arguments, user.id)
        module = spec.route.endpoint.__module__.rsplit(".", 1)[-1]
        if module in {"product_publish", "items", "ai_listing", "cards"}:
            check_media_references(arguments.get("body"))
        values = {**arguments.get("path", {}), **spec.bound}
        path = spec.route.path
        for name, value in values.items():
            value = str(value)
            if value in {".", ".."} or re.search(r"[/\\%?#\x00-\x1f]", value):
                raise ValueError("路径参数包含不允许的字符")
            path = path.replace("{" + name + "}", quote(value, safe=""))
        if "{" in path:
            raise ValueError("缺少路径参数")

        async def scoped_app(scope, receive, send):
            # Set by our closure, not by client input or network headers.
            await self.app({**scope, "mcp_owner": user}, receive, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=scoped_app), base_url="http://mcp-internal") as client:
            with ExitStack() as stack:
                kwargs = {"params": arguments.get("query", {})}
                body = arguments.get("body")
                if spec.file_fields:
                    form, uploads = {}, []
                    for key, value in (body or {}).items():
                        if key in spec.file_fields and value is not None:
                            for item in value if isinstance(value, list) else [value]:
                                record, blob = files.lookup(user.id, item["upload_id"])
                                handle = stack.enter_context(blob.open("rb"))
                                uploads.append((key, (record["filename"], handle, record["content_type"])))
                        elif value is not None:
                            form[key] = (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value))
                    kwargs.update(data=form, files=uploads)
                elif spec.content_type == "application/x-www-form-urlencoded":
                    kwargs["data"] = body
                elif body is not None:
                    kwargs["json"] = body
                response = await client.request(spec.method, path, **kwargs)
        try:
            data = redact(response.json(), {"value"} if spec.route.endpoint.__module__.endswith(".cookies") else set())
        except ValueError:
            return {"success": False, "code": "INVALID_RESPONSE", "message": "业务接口未返回 JSON"}
        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            return {"success": False, "code": str(response.status_code), "message": detail if isinstance(detail, str) else "业务接口拒绝了请求"}
        if not isinstance(data, dict):
            data = {"success": True, "data": data}
        elif "success" not in data:
            data = {"success": not bool(data.get("error")), "data": data}
        if len(json.dumps(data, ensure_ascii=False).encode()) > 1024 * 1024:
            result = await files.store_bytes(user.id, "result.json", json.dumps(data, ensure_ascii=False).encode(), "application/json")
            return {"success": data.get("success", True), "message": "结果超过 1 MiB，请通过文件接口读取完整 JSON", "data": {"file": result}}
        return data
