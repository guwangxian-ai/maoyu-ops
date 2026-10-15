"""Opt-in receipt integration against a disposable MySQL database only."""
from __future__ import annotations

import ast
import asyncio
from datetime import timedelta
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.mcp.auth import utcnow
from app.mcp.operations import OperationStore
from common.models.mcp_access import McpOperation


@unittest.skipUnless(os.environ.get("TEST_MCP_DATABASE_URL"), "requires disposable MySQL")
class MySqlReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = os.environ["TEST_MCP_DATABASE_URL"]
        if not (make_url(url).database or "").startswith("mcp_isolated_"):
            raise ValueError("Refusing non-test database")
        self.engines = [create_async_engine(url, pool_size=5) for _ in range(2)]
        self.sessions = [async_sessionmaker(e, expire_on_commit=False) for e in self.engines]
        self.stores = [OperationStore(s) for s in self.sessions]
        # Exercise the exact additive startup DDL used in production.
        source = Path(__file__).resolve().parents[2] / "common/db/init_database.py"
        ddl = next(ast.literal_eval(v) for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig")))
                   if isinstance(node, ast.Dict) for k, v in zip(node.keys, node.values)
                   if isinstance(k, ast.Constant) and k.value == "xy_mcp_operations")
        async with self.engines[0].begin() as conn:
            await conn.execute(text(ddl))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS receipt_effects (id VARCHAR(32) PRIMARY KEY, operation_id VARCHAR(160))"))
        self.access = SimpleNamespace(subject="1", client_id="one", scopes=["delivery:cards:write", "delivery:read"])
        self.operation_id = "mysql:" + uuid4().hex

    async def asyncTearDown(self):
        for store in self.stores: await store.close()
        for engine in self.engines: await engine.dispose()

    async def test_independent_connections_dispatch_once_and_survive_reconnect(self):
        async def effect():
            async with self.engines[0].begin() as conn:
                await conn.execute(text("INSERT INTO receipt_effects VALUES (:id, :op)"), {"id": uuid4().hex, "op": self.operation_id})
            await asyncio.sleep(0.1)
            return {"success": True, "data": {"id": 11}}
        async def call(i):
            return await self.stores[i % 2].run(self.access, self.operation_id, "cards_create_card", "delivery:cards:write", {"body": {"name": "isolated"}}, effect)
        results = await asyncio.gather(*(call(i) for i in range(10)))
        self.assertTrue(any(r.get("success") for r in results))
        async with self.engines[0].connect() as conn:
            count = await conn.scalar(text("SELECT COUNT(*) FROM receipt_effects WHERE operation_id=:op"), {"op": self.operation_id})
        self.assertEqual(count, 1)
        await self.engines[1].dispose()
        recovered = await self.stores[1].get(self.access, self.operation_id)
        self.assertEqual(recovered["data"]["state"], "observed")
        replay = await call(1)
        self.assertTrue(replay["mcp_operation"]["replayed"])
        self.assertEqual(replay["data"], {"id": 11})

    async def test_server_restart_marker_stays_uncertain_and_parameter_conflicts_block(self):
        async with self.sessions[0]() as session:
            from app.mcp.operations import fingerprint
            session.add(McpOperation(id=uuid4().hex, owner_id=1, token_id="dead-worker", operation_id=self.operation_id,
                tool_name="cards_create_card", required_scope="delivery:cards:write", state="running",
                request_hash=fingerprint("cards_create_card", {}), lease_expires_at=utcnow()-timedelta(seconds=5)))
            await session.commit()
        async def forbidden():
            raise AssertionError("Unknown operation must not be re-executed")
        result = await self.stores[1].run(self.access, self.operation_id, "cards_create_card", "delivery:cards:write", {}, forbidden)
        self.assertEqual(result["code"], "OPERATION_UNCERTAIN")
        conflict = await self.stores[0].run(self.access, self.operation_id, "cards_create_card", "delivery:cards:write", {"different": True}, forbidden)
        self.assertEqual(conflict["code"], "OPERATION_CONFLICT")


if __name__ == "__main__": unittest.main()
