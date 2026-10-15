"""Skill runner invariants. No production account or external writes."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from mcp import types

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mcp_ops", ROOT / "skills/xianyu-operations/scripts/mcp_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class OpsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config = {"url": "http://127.0.0.1:8089/mcp", "token": "test-private-value"}
        self.args = argparse.Namespace(execute=True, operation_id="order:one:deliver",
            authorization="isolated unit test", ledger=self.path / "receipts.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    async def test_preview_never_invokes_write_or_creates_ledger(self):
        self.args.execute = False
        invoke = AsyncMock()
        result = await ops.guarded_write(self.config, self.args, "publish", {}, invoke)
        self.assertTrue(result["preview"])
        invoke.assert_not_awaited()
        self.assertFalse(self.args.ledger.exists())

    async def test_missing_authorization_prevents_write(self):
        self.args.authorization = None
        invoke = AsyncMock()
        with self.assertRaises(ops.OpsError):
            await ops.guarded_write(self.config, self.args, "publish", {}, invoke)
        invoke.assert_not_awaited()

    async def test_observed_write_replays_without_calling_provider(self):
        invoke = AsyncMock(return_value={"ok": True, "result": {"batch_id": "one"}})
        first = await ops.guarded_write(self.config, self.args, "publish", {"price": 1}, invoke)
        second = await ops.guarded_write(self.config, self.args, "publish", {"price": 1}, invoke)
        self.assertEqual(invoke.await_count, 1)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["result"], second["result"])

    async def test_business_error_is_preserved_and_not_automatically_resent(self):
        invoke = AsyncMock(return_value={"ok": False, "result": {"code": "PARTIAL"}})
        await ops.guarded_write(self.config, self.args, "share", {}, invoke)
        result = await ops.guarded_write(self.config, self.args, "share", {}, invoke)
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"]["code"], "PARTIAL")
        invoke.assert_awaited_once()

    async def test_uncertain_write_cannot_retry(self):
        invoke = AsyncMock(side_effect=TimeoutError("private transport details"))
        with self.assertRaises(TimeoutError):
            await ops.guarded_write(self.config, self.args, "deliver", {}, invoke)
        with self.assertRaises(ops.OpsError):
            await ops.guarded_write(self.config, self.args, "deliver", {}, invoke)
        invoke.assert_awaited_once()
        ledger = ops.Ledger(self.args.ledger)
        self.assertEqual(ledger.get(self.args.operation_id)["state"], "uncertain")
        self.assertNotIn("private transport", json.dumps(ledger.get(self.args.operation_id)))
        ledger.close()

    async def test_same_id_different_input_or_identity_is_blocked(self):
        invoke = AsyncMock(return_value={"ok": True})
        await ops.guarded_write(self.config, self.args, "publish", {"price": 1}, invoke)
        for config, arguments in [(self.config, {"price": 2}),
                                  ({**self.config, "token": "another-token"}, {"price": 1})]:
            with self.assertRaises(ops.OpsError):
                await ops.guarded_write(config, self.args, "publish", arguments, invoke)
        invoke.assert_awaited_once()
        self.assertNotIn(self.config["token"].encode(), self.args.ledger.read_bytes())

    async def test_two_connections_cannot_reserve_same_inflight_operation(self):
        first, second = ops.Ledger(self.args.ledger), ops.Ledger(self.args.ledger)
        self.assertIsNone(first.reserve("op", "hash", "publish", "approval"))
        with self.assertRaises(ops.OpsError):
            second.reserve("op", "hash", "publish", "approval")
        first.close()
        second.close()
        self.assertEqual(self.args.ledger.stat().st_mode & 0o777, 0o600)

    async def test_actual_schema_blocks_bad_parameters_before_call(self):
        tool = types.Tool(name="write", inputSchema={"type": "object", "required": ["confirm"],
                          "properties": {"confirm": {"const": True}}, "additionalProperties": False})
        self.args.tool, self.args.arguments = "write", self.path / "arguments.json"
        self.args.arguments.write_text('{"confirm":false}')
        remote = AsyncMock()
        with self.assertRaises(ops.OpsError):
            await ops.call(remote, {"write": tool}, self.config, self.args)
        remote.call_tool.assert_not_awaited()

    async def test_read_only_tool_uses_real_response_without_write_authorization(self):
        tool = types.Tool(name="read", inputSchema={"type": "object"},
                          annotations=types.ToolAnnotations(readOnlyHint=True))
        self.args.tool, self.args.arguments, self.args.authorization = "read", None, None
        remote = AsyncMock()
        remote.call_tool.return_value = types.CallToolResult(content=[], structuredContent={"success": True, "data": []})
        result = await ops.call(remote, {"read": tool}, self.config, self.args)
        self.assertTrue(result["ok"])
        remote.call_tool.assert_awaited_once_with("read", {})
        self.assertFalse(self.args.ledger.exists())

    async def test_unannotated_tool_is_not_treated_as_read_only(self):
        tool = types.Tool(name="unknown", inputSchema={"type": "object"})
        self.args.tool, self.args.arguments, self.args.execute = "unknown", None, False
        remote = AsyncMock()
        result = await ops.call(remote, {"unknown": tool}, self.config, self.args)
        self.assertTrue(result["preview"])
        remote.call_tool.assert_not_awaited()

    async def test_transport_config_and_private_output(self):
        path = self.path / "config.json"
        for url in ["http://remote.test/mcp", "https://user:password@example.test/mcp", "https://example.test/mcp?token=x"]:
            path.write_text(json.dumps({"url": url, "token": "test"}))
            with self.assertRaises(ops.OpsError): ops.load_config(path)
        path.write_text(json.dumps(self.config))
        self.assertEqual(ops.load_config(path), self.config)
        output = self.path / "new" / "result.json"
        ops.private_json(output, {"links": ["private"]})
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)

    async def test_local_interruption_recovers_from_server_without_write(self):
        invoke = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            await ops.guarded_write(self.config, self.args, "deliver", {}, invoke)
        recovered = {"ok": True, "result": {"success": True, "data": {"id": 5},
                     "mcp_operation": {"state": "observed"}}}
        recover = AsyncMock(return_value=recovered)
        result = await ops.guarded_write(self.config, self.args, "deliver", {}, invoke, recover)
        self.assertTrue(result["replayed"])
        self.assertEqual(result["result"]["data"]["id"], 5)
        again = await ops.guarded_write(self.config, self.args, "deliver", {}, invoke, recover)
        self.assertTrue(again["ok"])
        invoke.assert_awaited_once()
        recover.assert_awaited_once()

    async def test_running_receipt_stays_uncertain_and_never_redispatches(self):
        invoke = AsyncMock(return_value={"ok": False, "result": {"mcp_operation": {"state": "running"}}})
        await ops.guarded_write(self.config, self.args, "deliver", {}, invoke)
        recover = AsyncMock(return_value={"ok": False, "message": "still running"})
        pending = await ops.guarded_write(self.config, self.args, "deliver", {}, invoke, recover)
        self.assertFalse(pending["ok"])
        invoke.assert_awaited_once()
        ledger = ops.Ledger(self.args.ledger)
        self.assertEqual(ledger.get(self.args.operation_id)["state"], "uncertain")
        ledger.close()

    async def test_write_injects_stable_server_id_and_conflicting_id_is_rejected(self):
        tool = types.Tool(name="write", inputSchema={"type": "object", "required": ["confirm", "operation_id"],
            "properties": {"confirm": {"const": True}, "operation_id": {"type": "string"}}})
        self.args.tool, self.args.arguments = "write", self.path / "arguments.json"
        self.args.arguments.write_text('{"confirm":true}')
        remote = AsyncMock()
        remote.call_tool.return_value = types.CallToolResult(content=[], structuredContent={"success": True})
        await ops.call(remote, {"write": tool}, self.config, self.args)
        remote.call_tool.assert_awaited_once_with("write", {"confirm": True, "operation_id": self.args.operation_id})
        self.args.arguments.write_text('{"confirm":true,"operation_id":"different"}')
        with self.assertRaises(ops.OpsError):
            await ops.call(remote, {"write": tool}, self.config, self.args)
        remote.call_tool.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
