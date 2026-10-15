#!/usr/bin/env python3
"""Small, portable MCP runner with private receipts and conservative write replay."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class OpsError(Exception):
    """Messages here are safe to display; transport exceptions are not."""


class UncertainOperation(OpsError):
    """A local receipt needs read-only reconciliation with the server."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def load_config(path: Path):
    config = json.loads(path.read_text())
    url, token = config.get("url", "").rstrip("/"), config.get("token", "")
    parsed = urlsplit(url)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (parsed.scheme != "https" and not (local and parsed.scheme == "http")):
        raise OpsError("配置必须使用 HTTPS；仅本机地址允许 HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.endswith("/mcp"):
        raise OpsError("配置 url 必须为无用户名、查询参数和片段的 /mcp 地址")
    if not isinstance(token, str) or not token.strip():
        raise OpsError("私有配置缺少 MCP token")
    return {"url": url, "token": token}


def identity(config):
    return hashlib.sha256(encode(config).encode()).hexdigest()


class Ledger:
    """Private client cache; durable owner-wide arbitration lives on the server.

    Historical servers only have this local guard. Neither mechanism deduplicates
    business intents submitted under different operation IDs.
    """
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, tool TEXT NOT NULL,
            authorization TEXT NOT NULL, state TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, response TEXT)""")
        self.db.commit()

    def get(self, operation_id):
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if not row:
            raise OpsError("此操作没有本地回执；这不能证明平台上没有执行过")
        result = dict(row)
        result.pop("fingerprint")
        result["response"] = json.loads(result["response"]) if result["response"] else None
        return result

    def reserve(self, operation_id, fingerprint, tool, authorization):
        if not operation_id or len(operation_id) > 160 or not authorization.strip():
            raise OpsError("写入需要稳定的 operation-id 和已有授权的文字引用")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise OpsError("操作编号已有不同参数或连接身份；请核对原回执，不能覆盖")
                if row["state"] != "observed":
                    raise UncertainOperation("原操作执行结果未知或仍在执行；先只读查询平台/任务，不能重发")
                response = json.loads(row["response"])
                response["replayed"] = True
                return response
            self.db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, 'started', ?, NULL, NULL)",
                            (operation_id, fingerprint, tool, authorization, now()))
            return None
        finally:
            self.db.commit()

    def finish(self, operation_id, state, response):
        self.db.execute("UPDATE operations SET state=?, finished_at=?, response=? WHERE id=?",
                        (state, now(), encode(response), operation_id))
        self.db.commit()

    def close(self):
        self.db.close()


@asynccontextmanager
async def connect(config):
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + config["token"]},
                                 timeout=httpx.Timeout(1800, connect=15), follow_redirects=False) as http:
        async with streamable_http_client(config["url"], http_client=http) as (read, write, _):
            async with ClientSession(read, write) as remote:
                await remote.initialize()
                yield remote


async def tool_map(remote):
    result, cursor = {}, None
    while True:
        page = await remote.list_tools(cursor=cursor)
        result.update({t.name: t for t in page.tools})
        cursor = page.nextCursor
        if not cursor:
            return result


def validate(tool, arguments):
    error = next(Draft202012Validator(tool.inputSchema).iter_errors(arguments), None)
    if error:
        field = ".".join(str(v) for v in error.absolute_path) or "root"
        raise OpsError(f"参数不符合 {tool.name} 定义：{field} / {error.validator}")


def unpack(response):
    data = response.structuredContent
    if data is None:
        data = {"content": [part.model_dump(mode="json") for part in response.content]}
    # A returned task ID or success response is only an observed call receipt.
    receipt = data.get("mcp_operation") or {}
    return {"ok": not response.isError and data.get("success") is not False,
            "observed_at": receipt.get("finished_at") if receipt.get("replayed") else now(), "result": data}


async def guarded_write(config, args, tool, arguments, invoke, recover=None):
    if not args.execute:
        return {"ok": True, "preview": True, "tool": tool,
                "message": "参数已校验，未执行。确认已有授权范围后，提供 --execute、--operation-id 和 --authorization。"}
    if not args.operation_id or not args.authorization:
        raise OpsError("写入需要 --operation-id 和 --authorization；参数不会替代用户授权")
    fingerprint = hashlib.sha256(encode([identity(config), tool, arguments]).encode()).hexdigest()
    ledger = Ledger(args.ledger)
    try:
        try:
            previous = ledger.reserve(args.operation_id, fingerprint, tool, args.authorization)
        except UncertainOperation:
            if recover is None:
                raise
            recovered = await recover()
            if recovered.get("result", {}).get("mcp_operation", {}).get("state") == "observed":
                recovered.update(tool=tool, operation_id=args.operation_id, replayed=True)
                ledger.finish(args.operation_id, "observed", recovered)
            return recovered
        if previous is not None:
            return previous
        try:
            result = await invoke()
            receipt = result.get("result", {}).get("mcp_operation", {})
            result.update(tool=tool, operation_id=args.operation_id, replayed=receipt.get("replayed", False))
            state = "uncertain" if receipt.get("state") in {"running", "uncertain"} or result.get("result", {}).get("code") in {"RECEIPT_PERSISTENCE_UNCERTAIN", "RECEIPT_UNAVAILABLE", "OPERATION_UNCERTAIN", "OPERATION_IN_PROGRESS"} else "observed"
            ledger.finish(args.operation_id, state, result)
            return result
        except BaseException:
            ledger.finish(args.operation_id, "uncertain", {"ok": False, "message": "调用中断；需查询原业务结果"})
            raise
    finally:
        ledger.close()


async def call(remote, tools, config, args):
    tool = tools.get(args.tool)
    if tool is None:
        raise OpsError("当前 MCP 没有此工具或令牌缺少权限；用 schema/doctor 检查")
    arguments = json.loads(args.arguments.read_text()) if args.arguments else {}
    server_receipts = "operation_id" in tool.inputSchema.get("properties", {}) and not (tool.annotations and tool.annotations.readOnlyHint is True)
    if server_receipts:
        if arguments.get("operation_id") and arguments["operation_id"] != args.operation_id:
            raise OpsError("参数文件和 --operation-id 必须一致，不能更换编号重试")
        arguments["operation_id"] = args.operation_id or ("preview:validation-only" if not args.execute else "")
    validate(tool, arguments)
    async def invoke():
        return unpack(await remote.call_tool(tool.name, arguments))
    if tool.annotations and tool.annotations.readOnlyHint is True:
        return {"tool": tool.name, **await invoke()}
    recover = (lambda: recover_write(remote, args.operation_id)) if server_receipts else None
    return await guarded_write(config, args, tool.name, arguments, invoke, recover)


async def recover_write(remote, operation_id):
    response = unpack(await remote.call_tool("mcp_operation_get", {"operation_id": operation_id}))
    data = response.get("result", {}).get("data", {})
    if data.get("state") == "observed" and data.get("result") is not None:
        result = data["result"]
        return {"ok": result.get("success") is not False, "observed_at": data.get("finished_at"),
                "result": {**result, "mcp_operation": {k: v for k, v in data.items() if k not in {"result", "request_hash"}}},
                "replayed": True}
    return {"ok": False, "recovery": response, "message": "尚无可恢复的完成回执；继续查询原操作或只读核对平台，未再次执行"}


async def upload(config, args, remote=None):
    path = args.file
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise OpsError("上传需要不超过 256 MiB 的普通文件")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        metadata = {"filename": path.name, "size": path.stat().st_size, "sha256": digest}
        async def invoke():
            # Same open file descriptor as the hash; verify again before dispatch.
            handle.seek(0)
            if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                raise OpsError("上传文件在校验后发生变化，未发送")
            handle.seek(0)
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + config["token"]},
                                         timeout=1800, follow_redirects=False) as http:
                response = await http.post(config["url"][:-4] + "/api/v1/mcp/files",
                                           headers={"X-MCP-Operation-Id": args.operation_id},
                                           files={"file": (path.name, handle, "application/octet-stream")})
                response.raise_for_status()
                result = response.json()
                return {"ok": result.get("success") is not False, "observed_at": now(),
                        "result": result, "file": metadata}
        return await guarded_write(config, args, "mcp_http_upload", metadata, invoke,
                                   (lambda: recover_write(remote, args.operation_id)) if remote else None)


async def doctor(remote, tools):
    results = {}
    for name in ("mcp_capabilities", "account_list_cookie_options", "assets_status", "quark_capabilities", "baidu_capabilities"):
        if name in tools:
            results[name] = unpack(await remote.call_tool(name, {}))
        else:
            results[name] = {"ok": False, "message": "工具不可用或权限不足"}
    return {"ok": all(v["ok"] for v in results.values()), "checked_at": now(),
            "tool_count": len(tools), "checks": results,
            "message": "连接检查；发布能力需选定账号后单独查询，订单发货需检查对应订单与 WebSocket 状态。"}


async def run(args):
    if args.action == "receipt":
        ledger = Ledger(args.ledger)
        try:
            return {"ok": True, **ledger.get(args.operation_id)}
        finally:
            ledger.close()
    config = load_config(args.config)
    async with connect(config) as remote:
        tools = await tool_map(remote)
        if args.action == "resume":
            if "mcp_operation_get" not in tools:
                raise OpsError("此服务器尚不支持服务端回执；查询本地 receipt 并核对原业务")
            return {"operation_id": args.operation_id, **unpack(await remote.call_tool("mcp_operation_get", {"operation_id": args.operation_id}))}
        if args.action == "operations":
            return unpack(await remote.call_tool("mcp_operations_list", {"limit": args.limit}))
        if args.action == "upload":
            return await upload(config, args, remote if "mcp_operation_get" in tools else None)
        if args.action == "schema":
            names = args.tool or sorted(tools)
            if any(n not in tools for n in names):
                raise OpsError("指定工具不在当前权限的工具列表内")
            return {"ok": True, "tools": [tools[n].model_dump(mode="json", exclude_none=True) for n in names]}
        if args.action == "doctor":
            return await doctor(remote, tools)
        return await call(remote, tools, config, args)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=Path(os.getenv("XIANYU_MCP_CONFIG", "data/local-dev/mcp-client.json")))
    p.add_argument("--ledger", type=Path, default=Path("data/ops-runs/receipts.sqlite3"))
    p.add_argument("--output", type=Path, help="Save private JSON instead of printing business data")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="Read account, drive authorization and capability checks")
    schema = sub.add_parser("schema", help="Discover actual tools and schemas")
    schema.add_argument("--tool", action="append")
    receipt = sub.add_parser("receipt", help="Inspect an existing write receipt, without network")
    receipt.add_argument("operation_id")
    resume = sub.add_parser("resume", help="Read durable server receipt after interruption; never dispatch the write again")
    resume.add_argument("operation_id")
    operations = sub.add_parser("operations", help="List accessible server write receipts")
    operations.add_argument("--limit", type=int, default=30, choices=range(1, 101), metavar="1..100")
    invoke = sub.add_parser("call")
    invoke.add_argument("tool")
    invoke.add_argument("--arguments", type=Path)
    file = sub.add_parser("upload", help="Stage a real local file through the MCP file API")
    file.add_argument("--file", type=Path, required=True)
    for command in (invoke, file):
        command.add_argument("--execute", action="store_true")
        command.add_argument("--operation-id")
        command.add_argument("--authorization", help="Reference to existing user approval or operating policy")
    return p


def main():
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
        if args.output:
            private_json(args.output, result)
            print(encode({"ok": result.get("ok"), "saved": str(args.output.resolve()),
                          "preview": result.get("preview", False), "replayed": result.get("replayed", False)}))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except OpsError as exc:
        print(encode({"ok": False, "error": str(exc)}), file=sys.stderr)
    except Exception as exc:
        # Exception text can include URL parameters, tokens or order details.
        print(encode({"ok": False, "error_type": type(exc).__name__,
                      "message": "调用未完成；写入先查 receipt 和业务状态，不要换编号盲目重试。"}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    sys.exit(main())
