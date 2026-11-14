"""公共所在地查询传输，适配上游 1e48313；日志不保存参数、请求头或响应正文。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from loguru import logger

HTTP_TIMEOUT_SECONDS = 30
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_DELAY_SECONDS = 1.0


async def request_inputtips(url: str, *, params: dict[str, str], headers: dict[str, str]) -> Any:
    """沿用三次有界重试与退避；调用方控制整个查询的总超时，退出时关闭会话。"""
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(HTTP_MAX_ATTEMPTS):
            try:
                async with session.get(url, params=params, headers=headers) as response:
                    body_text = await response.text()
                    if response.status >= 500:
                        raise aiohttp.ClientError(f"服务器错误 {response.status}")
                    try:
                        body = json.loads(body_text)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        body = {"text": body_text}
                    if response.status >= 400 or not isinstance(body, dict) or str(body.get("status")) != "1":
                        logger.warning("高德查询失败: status={}, response_bytes={}, attempt={}",
                                       response.status, len(body_text), attempt + 1)
                    return body
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("高德查询连接失败: error_type={}, attempt={}/{}",
                               type(exc).__name__, attempt + 1, HTTP_MAX_ATTEMPTS)
                if attempt + 1 == HTTP_MAX_ATTEMPTS:
                    if isinstance(exc, asyncio.TimeoutError):
                        raise asyncio.TimeoutError("高德查询超时") from None
                    raise aiohttp.ClientError("高德查询连接失败") from None
                await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (2 ** attempt))
