#!/usr/bin/env python3
"""Configure/test the private MCP, or bridge it to stdio-only AI clients.

Uses the project's existing official mcp/httpx dependencies. Credentials are
read from a private config file, never from command-line arguments or stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import logging
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server


def secure_config(path: Path, config: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)


def init(args):
    base = args.url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("远程连接必须使用 HTTPS；本机可使用 HTTP")
    if args.config.exists():
        raise ValueError("配置文件已存在，请使用其他路径，或先撤销原令牌")
    with httpx.Client(timeout=30) as http:
        if args.website_token_file:
            jwt = args.website_token_file.read_text().strip()
        else:
            login = http.post(base + "/api/v1/auth/login", json={"username": args.username, "password": getpass.getpass("本站登录密码: ")}).json()
            if not login.get("success") or not login.get("token"):
                raise ValueError(login.get("message") or "登录失败。如需验证码，请先在网页完成登录，再提供 --website-token-file")
            jwt = login["token"]
        response = http.post(base + "/api/v1/mcp/tokens", headers={"Authorization": "Bearer " + jwt},
                             json={"name": args.name, "scopes": args.scopes, "expires_in_days": args.days})
        data = response.json()
        if not data.get("success"):
            raise ValueError(data.get("message") or "创建 MCP 令牌失败")
        payload = data["data"]
        try:
            secure_config(args.config, {"url": base + "/mcp", "token": payload["token"],
                                       "token_id": payload["id"], "expires_at": payload["expires_at"], "scopes": payload["scopes"]})
        except BaseException:
            http.delete(base + "/api/v1/mcp/tokens/" + payload["id"], headers={"Authorization": "Bearer " + jwt})
            raise
    print(json.dumps({"saved": str(args.config.resolve()), "url": base + "/mcp", "expires_at": payload["expires_at"]}, ensure_ascii=False))


async def connect(args):
    config = json.loads(args.config.read_text())
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + config["token"]},
                                 timeout=httpx.Timeout(1800, connect=15)) as http:
        async with streamable_http_client(config["url"], http_client=http) as (read, write, _):
            async with ClientSession(read, write) as remote:
                initialized = await remote.initialize()
                if args.action == "tools":
                    data = await remote.list_tools()
                    print(json.dumps([{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in data.tools], ensure_ascii=False, indent=2))
                elif args.action == "call":
                    arguments = json.loads(args.arguments.read_text()) if args.arguments else {}
                    data = await remote.call_tool(args.tool, arguments)
                    print(data.model_dump_json(by_alias=True))
                    if data.isError:
                        raise SystemExit(1)
                else:
                    proxy = Server("xianyu-remote-bridge", instructions=initialized.instructions)
                    @proxy.list_tools()
                    async def list_tools():
                        return (await remote.list_tools()).tools
                    @proxy.call_tool(validate_input=False)
                    async def call_tool(name, arguments):
                        return await remote.call_tool(name, arguments)
                    @proxy.list_resources()
                    async def list_resources():
                        return (await remote.list_resources()).resources
                    @proxy.read_resource()
                    async def read_resource(uri):
                        data = await remote.read_resource(uri)
                        return [ReadResourceContents(content=part.text if hasattr(part, "text") else base64.b64decode(part.blob), mime_type=part.mimeType)
                                for part in data.contents]
                    async with stdio_server() as (input_stream, output_stream):
                        await proxy.run(input_stream, output_stream, proxy.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("data/local-dev/mcp-client.json"))
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser("init", help="Login and issue a scoped token (saved privately)")
    setup.add_argument("--url", default="http://127.0.0.1:8089")
    setup.add_argument("--username", default="admin")
    setup.add_argument("--website-token-file", type=Path)
    setup.add_argument("--name", default="AI client")
    setup.add_argument("--days", type=int, default=30)
    setup.add_argument("--scopes", nargs="+", default=["xianyu:read", "drive:read", "delivery:read", "messages:read"])
    sub.add_parser("tools", help="List available tools and their schemas")
    call = sub.add_parser("call", help="Call one tool with arguments from a JSON file")
    call.add_argument("tool")
    call.add_argument("--arguments", type=Path)
    sub.add_parser("serve", help="Expose the remote MCP over stdio; stdout is protocol only")
    args = parser.parse_args()
    try:
        if args.action == "init": init(args)
        else: asyncio.run(connect(args))
    except (ValueError, OSError, httpx.HTTPError) as exc:
        print(f"MCP 客户端失败: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    main()
