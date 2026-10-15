"""Durable recovery and capability isolation, with isolated SQLite and no providers."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend-web"), str(Path(__file__).parent)]

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import test_mcp_gateway as gateway_tests
from test_mcp_gateway import Db
from app.mcp import auth
from app.mcp.operations import OperationStore, fingerprint
from app.mcp.permissions import expand_legacy_scopes, permits
from common.models.mcp_access import McpOperation


class OperationStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        McpOperation.__table__.create(self.engine)
        self.store = OperationStore(lambda: Db(self.engine))
        self.access = SimpleNamespace(subject="1", client_id="client-one", scopes=["delivery:cards:write", "delivery:read"])
        self.args = {"body": {"name": "card"}, "confirm": True, "operation_id": "card:one"}

    async def asyncTearDown(self):
        await self.store.close()
        self.engine.dispose()

    async def run_write(self, invoke, store=None, access=None, arguments=None):
        return await (store or self.store).run(access or self.access, "card:one", "cards_create_card",
            "delivery:cards:write", arguments or self.args, invoke)

    async def test_completed_result_survives_new_store_and_new_client_without_redispatch(self):
        invoke = AsyncMock(return_value={"success": True, "data": {"id": 17}})
        first = await self.run_write(invoke)
        other = SimpleNamespace(**{**vars(self.access), "client_id": "client-two"})
        second = await self.run_write(invoke, store=OperationStore(lambda: Db(self.engine)), access=other)
        self.assertEqual(invoke.await_count, 1)
        self.assertFalse(first["mcp_operation"]["replayed"])
        self.assertTrue(second["mcp_operation"]["replayed"])
        self.assertEqual(second["data"], {"id": 17})
        self.assertEqual(second["mcp_operation"]["finished_at"], first["mcp_operation"]["finished_at"])

    async def test_concurrent_clients_cannot_dispatch_same_operation_twice(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
            return {"success": True}
        invoke = AsyncMock(side_effect=slow)
        first = asyncio.create_task(self.run_write(invoke))
        await entered.wait()
        second = await self.run_write(invoke, store=OperationStore(lambda: Db(self.engine)))
        self.assertEqual(second["code"], "OPERATION_IN_PROGRESS")
        self.assertEqual(invoke.await_count, 1)
        release.set()
        await first

    async def test_disconnect_does_not_cancel_dispatched_write_and_result_is_recoverable(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
            return {"success": True, "data": {"job_id": "known-job"}}
        caller = asyncio.create_task(self.run_write(slow))
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError): await caller
        self.assertEqual((await self.store.get(self.access, "card:one"))["data"]["state"], "running")
        release.set()
        await asyncio.gather(*tuple(self.store.tasks))
        recovered = (await self.store.get(self.access, "card:one"))["data"]
        self.assertEqual(recovered["state"], "observed")
        self.assertEqual(recovered["result"]["data"]["job_id"], "known-job")

    async def test_expired_lease_becomes_uncertain_without_takeover(self):
        with Session(self.engine) as session:
            session.add(McpOperation(id="orphan", owner_id=1, token_id="old", operation_id="card:one",
                tool_name="cards_create_card", required_scope="delivery:cards:write",
                request_hash=fingerprint("cards_create_card", self.args), state="running",
                lease_expires_at=auth.utcnow() - timedelta(seconds=1)))
            session.commit()
        invoke = AsyncMock()
        receipt = await self.run_write(invoke)
        self.assertEqual(receipt["code"], "OPERATION_UNCERTAIN")
        invoke.assert_not_awaited()
        self.assertEqual((await self.store.get(self.access, "card:one"))["data"]["state"], "uncertain")

    async def test_changed_arguments_and_owner_scope_are_separate(self):
        invoke = AsyncMock(return_value={"success": True})
        await self.run_write(invoke)
        conflict = await self.run_write(invoke, arguments={**self.args, "body": {"name": "changed"}})
        self.assertEqual(conflict["code"], "OPERATION_CONFLICT")
        other = SimpleNamespace(subject="2", client_id="other", scopes=self.access.scopes)
        self.assertEqual((await self.store.get(other, "card:one"))["code"], "OPERATION_NOT_FOUND")
        self.assertEqual((await self.store.listing(other))["data"]["operations"], [])
        limited = SimpleNamespace(subject="1", client_id="limited", scopes=["drive:read"])
        self.assertEqual((await self.store.get(limited, "card:one"))["code"], "OPERATION_NOT_FOUND")
        self.assertEqual((await self.store.listing(limited))["data"]["operations"], [])
        self.assertEqual(invoke.await_count, 1)

    async def test_provider_errors_are_preserved_and_exceptions_never_retry(self):
        invoke = AsyncMock(return_value={"success": False, "code": "PROVIDER_REJECTED"})
        first = await self.run_write(invoke)
        second = await self.run_write(invoke)
        self.assertEqual(second["code"], "PROVIDER_REJECTED")
        self.assertEqual(first["mcp_operation"]["state"], "observed")
        self.assertEqual(invoke.await_count, 1)
        broken = AsyncMock(side_effect=TimeoutError("secret transport text"))
        result = await self.store.run(self.access, "card:two", "cards_create_card", "delivery:cards:write", {}, broken)
        self.assertEqual(result["mcp_operation"]["state"], "uncertain")
        self.assertNotIn("secret", json.dumps(result))
        with Session(self.engine) as session:
            row = session.scalars(select(McpOperation).where(McpOperation.operation_id == "card:one")).one()
            self.assertEqual(row.request_hash, fingerprint("cards_create_card", self.args))
            self.assertFalse(hasattr(row, "arguments"))

    async def test_missing_id_never_dispatches(self):
        invoke = AsyncMock()
        result = await self.store.run(self.access, None, "cards_create_card", "delivery:cards:write", {}, invoke)
        self.assertEqual(result["code"], "OPERATION_ID_REQUIRED")
        invoke.assert_not_awaited()

    async def test_receipt_commit_failure_never_claims_retry_is_safe(self):
        invoke = AsyncMock(return_value={"success": True, "data": {"id": 3}})
        self.store.sessions = lambda: Db(self.engine)
        with patch.object(self.store, "sessions", side_effect=[Db(self.engine), RuntimeError("private db details")]):
            result = await self.run_write(invoke)
        self.assertEqual(result["code"], "RECEIPT_PERSISTENCE_UNCERTAIN")
        self.assertTrue(result["success"])
        self.assertNotIn("private db", json.dumps(result))
        retry = await self.run_write(invoke)
        self.assertEqual(retry["code"], "OPERATION_IN_PROGRESS")
        invoke.assert_awaited_once()


class GranularProtocolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = gateway_tests.McpProtocolTests.asyncSetUp
    asyncTearDown = gateway_tests.McpProtocolTests.asyncTearDown
    client = gateway_tests.McpProtocolTests.client

    async def test_all_legacy_domains_expand_without_changing_tools(self):
        for domain in ("xianyu", "drive", "delivery", "messages"):
            original = [domain + ":write"]
            self.assertEqual({t["name"] for t in self.gateway.metadata(original)},
                             {t["name"] for t in self.gateway.metadata(expand_legacy_scopes(original))})

    async def test_http_upload_uses_server_receipt_and_rejects_changed_content(self):
        import httpx
        from app.api import deps
        from app.api.routes import mcp_access
        self.app.include_router(mcp_access.router, prefix="/api/v1")
        self.app.state.mcp_gateway = self.gateway
        async def db_dep():
            async with Db(self.engine) as db: yield db
        self.app.dependency_overrides[deps.get_db_session] = db_dep
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1",
                                    headers={"Authorization": "Bearer " + self.full}) as client:
            files = {"file": ("sample.txt", b"one", "text/plain")}
            missing = (await client.post("/api/v1/mcp/files", files=files)).json()
            self.assertEqual(missing["code"], "OPERATION_ID_REQUIRED")
            headers = {"X-MCP-Operation-Id": "upload:test"}
            first = (await client.post("/api/v1/mcp/files", files=files, headers=headers)).json()
            second = (await client.post("/api/v1/mcp/files", files=files, headers=headers)).json()
            self.assertEqual(first["data"]["upload_id"], second["data"]["upload_id"])
            self.assertTrue(second["mcp_operation"]["replayed"])
            conflict = (await client.post("/api/v1/mcp/files", files={"file": ("sample.txt", b"two", "text/plain")}, headers=headers)).json()
            self.assertEqual(conflict["code"], "OPERATION_CONFLICT")
    async def test_card_configuration_cannot_send_order_or_enable_account(self):
        async with Db(self.engine) as db:
            _, secret = await auth.issue_token(db, 1, "cards only", ["delivery:read", "delivery:cards:write"], 1)
        async with self.client(secret) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("cards_create_card", names)
            self.assertNotIn("orders_manual_delivery", names)
            self.assertNotIn("account_update_account_status", names)
            result = await client.call_tool("orders_manual_delivery", {"body": {"order_no": "never-send"}, "confirm": True, "operation_id": "denied:order"})
            self.assertEqual(result.structuredContent["code"], "FORBIDDEN")

    async def test_account_enable_scope_cannot_edit_price_and_legacy_is_equivalent(self):
        original = ["xianyu:read", "xianyu:write"]
        expanded = expand_legacy_scopes(original)
        self.assertEqual({tool["name"] for tool in self.gateway.metadata(original)},
                         {tool["name"] for tool in self.gateway.metadata(expanded)})
        async with Db(self.engine) as db:
            _, secret = await auth.issue_token(db, 1, "account only", ["xianyu:account-status:write"], 1)
        async with self.client(secret) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("account_update_account_status", names)
            self.assertNotIn("items_update_item_price", names)
            self.assertNotIn("publish_publish_single", names)
            self.assertNotIn("mcp_upload_file", names)

    async def test_write_requires_id_and_receipts_are_queryable_over_mcp(self):
        async with self.client() as client:
            missing = await client.call_tool("cards_create_card", {"body": {"name": "test", "type": "text"}, "confirm": True})
            self.assertEqual(missing.structuredContent["code"], "INVALID_ARGUMENTS")
            args = {"body": {"name": "test", "type": "text"}, "confirm": True, "operation_id": "protocol:card"}
            with patch.object(self.gateway.bridge, "invoke", AsyncMock(return_value={"success": True, "data": {"id": 88}})) as invoke:
                first = await client.call_tool("cards_create_card", args)
                second = await client.call_tool("cards_create_card", args)
                self.assertEqual(invoke.await_count, 1)
                self.assertFalse(first.structuredContent["mcp_operation"]["replayed"])
                self.assertTrue(second.structuredContent["mcp_operation"]["replayed"])
                recovered = await client.call_tool("mcp_operation_get", {"operation_id": "protocol:card"})
                self.assertEqual(recovered.structuredContent["data"]["result"]["data"]["id"], 88)


if __name__ == "__main__": unittest.main()
