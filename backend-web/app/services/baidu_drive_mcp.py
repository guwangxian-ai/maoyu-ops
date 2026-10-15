"""Official Baidu SSE tools, using the maintained MCP SDK transport."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from urllib.parse import urlencode

from mcp import ClientSession
from mcp.client.sse import sse_client

from app.services.baidu_drive_auth import BaiduDriveError, check_provider


MCP_URL = "https://mcp-pan.baidu.com/sse"


def _check_tool_result(data: dict) -> None:
    check_provider(data)
    if str(data.get("code", 0)) not in {"0", "200", "None"}:
        raise BaiduDriveError(f"百度 MCP 请求失败（错误码 {str(data.get('code'))[:24]}）")
    if data.get("success") is False or data.get("status") in {"error", "failed"}:
        raise BaiduDriveError("百度网盘未完成此操作，请检查文件权限后重试")


def _json_result(result) -> dict:
    data = getattr(result, "structuredContent", None)
    if data is None:
        texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
        for text in texts:
            value = text.strip()
            if value.startswith("```") and value.endswith("```"):
                value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                data = json.loads(value)
                break
            except (ValueError, TypeError):
                continue
    if getattr(result, "isError", False):
        if isinstance(data, dict):
            check_provider(data)
        raise BaiduDriveError("百度 MCP 工具执行失败，请检查授权范围、应用权限或文件是否存在")
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if isinstance(data, list):
        return {"list": data}
    if not isinstance(data, dict):
        raise BaiduDriveError("百度 MCP 没有返回可验证的数据结果")
    _check_tool_result(data)
    payload = data.get("data")
    if isinstance(payload, list):
        return {"list": payload}
    if isinstance(payload, dict):
        _check_tool_result(payload)
        return payload
    return data


def _argument_value(value: object, schema: dict) -> object:
    alternatives = schema.get("anyOf", schema.get("oneOf", []))
    if alternatives:
        compatible = "array" if isinstance(value, list) else "object" if isinstance(value, dict) else "string" if isinstance(value, str) else "integer" if isinstance(value, int) else None
        selected = next((item for item in alternatives if item.get("type") == compatible), None)
        selected = selected or next((item for item in alternatives if item.get("type") != "null"), {})
        return _argument_value(value, selected)
    kind = schema.get("type")
    if kind == "string" and isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if kind in {"integer", "number"} and isinstance(value, str) and value.isdigit():
        return int(value)
    if kind == "string" and isinstance(value, (int, float, bool)):
        return json.dumps(value)
    if kind == "array" and isinstance(value, list):
        return [_argument_value(item, schema.get("items", {})) for item in value]
    return value


def tool_arguments(schema: dict, values: dict) -> dict:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise BaiduDriveError("百度 MCP 工具参数定义无效")
    arguments = {key: _argument_value(value, properties[key]) for key, value in values.items() if key in properties}
    for key in schema.get("required", []):
        if key not in arguments and "default" in properties.get(key, {}):
            arguments[key] = properties[key]["default"]
    missing = [key for key in schema.get("required", []) if key not in arguments]
    if missing:
        raise BaiduDriveError("百度 MCP 工具参数已变化，需要更新接口适配")
    return arguments


async def call_tool(access_token: str, name: str, values: dict) -> dict:
    """Never retry a mutation: after a timeout its result may be uncertain."""
    try:
        async with asyncio.timeout(150):
            async with sse_client(MCP_URL + "?" + urlencode({"access_token": access_token}),
                                  timeout=20, sse_read_timeout=120) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=120)) as session:
                    await session.initialize()
                    tools = []
                    cursor = None
                    for _ in range(10):
                        listed = await session.list_tools(cursor=cursor)
                        tools.extend(listed.tools)
                        cursor = listed.nextCursor
                        if not cursor:
                            break
                    matched = next((tool for tool in tools if tool.name == name), None)
                    # The upstream README accidentally gives text upload the URL
                    # tool's name. Resolve only a declared text-input tool.
                    if name == "file_upload_by_text":
                        matched = next((tool for tool in tools if "content" in tool.inputSchema.get("properties", {})
                                        and "upload" in tool.name.lower()), matched)
                    if not matched:
                        raise BaiduDriveError("百度当前服务未开放此能力，请检查应用权限或稍后重试")
                    arguments = tool_arguments(matched.inputSchema, values)
                    return _json_result(await session.call_tool(matched.name, arguments))
    except BaiduDriveError:
        raise
    except Exception as exc:
        # TaskGroup exceptions wrap the safe tool failures raised inside the
        # SDK contexts. Keep their message without exposing token-bearing URLs.
        pending = [exc]
        while pending:
            current = pending.pop(0)
            if isinstance(current, BaiduDriveError):
                raise current from exc
            pending.extend(getattr(current, "exceptions", ()))
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            raise BaiduDriveError("百度 MCP 请求超时；已提交的修改可能仍在处理，请刷新核验后再操作") from exc
        raise BaiduDriveError("百度 MCP 请求未能完成，请检查网络和授权；已提交的修改结果可能不明确，请刷新核验后再操作") from exc
