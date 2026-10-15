"""Real SDK transport, real business handlers, isolated ORM/files.

External providers are not contacted here. Live local verification is recorded
separately; fixtures never run in production.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend-web")]

import httpx
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from starlette.routing import Route

from app.api import api_router, deps
from app.mcp import auth, files, ownership, server
from app.mcp.bridge import redact, check_media_references
from app.mcp.catalog import build_catalog
from app.mcp.permissions import can_upload
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.mcp_access import McpAccessToken, McpToolLog, McpOperation
from common.models.user import User, UserRole, UserStatus
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem


@compiles(BigInteger, "sqlite")
def bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class Db:
    """Existing project test pattern: async facade over isolated real SQLite."""
    def __init__(self, engine):
        self.db = Session(engine, expire_on_commit=False)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.db.close()
    def add(self, row): self.db.add(row)
    async def get(self, *args, **kwargs): return self.db.get(*args, **kwargs)
    async def execute(self, *args, **kwargs): return self.db.execute(*args, **kwargs)
    async def scalars(self, *args, **kwargs): return self.db.scalars(*args, **kwargs)
    async def scalar(self, *args, **kwargs): return self.db.scalar(*args, **kwargs)
    async def commit(self): self.db.commit()
    async def flush(self): self.db.flush()
    async def refresh(self, row): self.db.refresh(row)
    async def rollback(self): self.db.rollback()


class McpCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = FastAPI()
        cls.app.include_router(api_router, prefix="/api/v1")
        cls.catalog = build_catalog(cls.app.routes)

    def test_real_routes_and_schemas_cover_business(self):
        self.assertGreater(len(self.catalog), 200)
        for name in ["publish_publish_single", "publish_publish_batch", "items_batch_offline_items",
                     "orders_manual_delivery", "cards_create_card", "chat_send_message", "search_search_items",
                     "quark_upload", "quark_create_share", "quark_move", "baidu_delete", "baidu_file_meta",
                     "assets_sync", "assets_shares", "assets_card", "assets_import_url"]:
            self.assertIn(name, self.catalog)
        for spec in self.catalog.values():
            Draft202012Validator.check_schema(spec.schema)
            self.assertTrue(spec.route.path.startswith("/api/v1/"))
        self.assertEqual(self.catalog["baidu_file_meta"].scope, "drive:read")
        self.assertEqual(self.catalog["baidu_delete"].scope, "drive:delete")
        self.assertEqual(self.catalog["assets_card"].scope, "delivery:cards:write")

    def test_no_admin_or_credential_routes(self):
        for spec in self.catalog.values():
            self.assertNotIn("/external/", spec.route.path)
            self.assertNotIn("/system-control", spec.route.path)
            self.assertNotIn("/admin", spec.route.path)
        self.assertNotIn("account_list_cookies", self.catalog)
        self.assertNotIn("quark_auth_complete", self.catalog)
        self.assertNotIn("baidu_auth_complete", self.catalog)

    def test_each_file_tool_can_stage_files_with_its_exact_grant(self):
        for spec in self.catalog.values():
            if spec.file_fields and not spec.readonly:
                with self.subTest(tool=spec.name):
                    self.assertTrue(can_upload([spec.scope, *spec.extra_scopes]))
        self.assertTrue(can_upload(["messages:send:write"]))
        self.assertTrue(can_upload(["xianyu:items:write"]))
        self.assertFalse(can_upload(["xianyu:account-status:write"]))
        self.assertFalse(can_upload(["drive:share:write"]))

    def test_nested_credentials_removed_without_losing_share_passcode(self):
        data = redact({"data": [{"cookie": "private", "api_key": "private", "passcode": "1234", "name": "sample"}], "refresh_token": "private"})
        self.assertEqual(data, {"data": [{"passcode": "1234", "name": "sample"}]})
        self.assertEqual(redact({"value": "cookie", "remark": "account"}, {"value"}), {"remark": "account"})

    def test_media_cannot_read_server_files_or_fetch_arbitrary_urls(self):
        for value in ["/etc/passwd", "~/.ssh/id_rsa", "/static/uploads/../../.env", "http://127.0.0.1/private", "https://evil.test/image.jpg"]:
            with self.assertRaises(ValueError): check_media_references({"images": [value]})
        check_media_references({"images": ["/static/uploads/products/image.png", "https://img.alicdn.com/image.jpg"]})

    def test_ai_settings_cannot_redirect_saved_credentials(self):
        spec = self.catalog["ai_bulk_update_ai_settings"]
        invalid = {"body": {"account": {"base_url": "https://evil.test"}}, "confirm": True, "operation_id": "test:" + uuid4().hex}
        self.assertTrue(list(Draft202012Validator(spec.schema).iter_errors(invalid)))


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite://")
        for model in [User, Card, CardItemRelation, XYAccount, XYCatalogItem, McpAccessToken, McpToolLog, McpOperation]:
            model.__table__.create(self.engine)
        with Session(self.engine) as db:
            db.add_all([User(id=1, username="owner", email="owner@example.test", password_hash="unused", role=UserRole.ADMIN, status=UserStatus.ACTIVE),
                        User(id=2, username="other", email="other@example.test", password_hash="unused", role=UserRole.MEMBER, status=UserStatus.ACTIVE),
                        Card(id=1, user_id=1, name="own", type="text", text_content="own content"),
                        Card(id=2, user_id=2, name="other", type="text", text_content="must not leak")])
            db.add_all([XYAccount(id=1, owner_id=1, account_id="mine", cookie="unused", login_method="cookie"),
                        XYAccount(id=2, owner_id=2, account_id="other", cookie="unused", login_method="cookie"),
                        XYCatalogItem(id=1, owner_id=1, account_pk=1, item_id="own-item", created_at=auth.utcnow()),
                        XYCatalogItem(id=2, owner_id=2, account_pk=2, item_id="other-item", created_at=auth.utcnow()),
                        CardItemRelation(user_id=2, card_id=2, item_id="other-item", source="own")])
            db.commit()
        self.patches = [patch.object(auth, "async_session_maker", lambda: Db(self.engine)),
                        patch.object(ownership, "async_session_maker", lambda: Db(self.engine)),
                        patch.object(server, "async_session_maker", lambda: Db(self.engine)),
                        patch.dict(os.environ, {"MCP_FILE_DIR": self.temp.name})]
        for p in self.patches: p.start()
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        self.gateway = server.McpGateway(app.routes)
        async def db_dep():
            async with Db(self.engine) as db: yield db
        self.gateway.bridge.app.dependency_overrides[deps.get_db_session] = db_dep
        self.app = FastAPI()
        self.app.router.routes.append(Route("/mcp", endpoint=self.gateway.asgi, methods=["GET", "POST", "DELETE"]))
        async with Db(self.engine) as db:
            self.full_row, self.full = await auth.issue_token(db, 1, "test", sorted(auth.SCOPES), 1)
            self.read_row, self.read = await auth.issue_token(db, 1, "read", ["drive:read", "delivery:read"], 1)
        self.manager_ready, self.manager_stop = asyncio.Event(), asyncio.Event()
        async def manager_lifetime():
            async with self.gateway.manager.run():
                self.manager_ready.set()
                await self.manager_stop.wait()
        self.manager_task = asyncio.create_task(manager_lifetime())
        await self.manager_ready.wait()

    async def asyncTearDown(self):
        await self.gateway.close()
        self.manager_stop.set()
        await self.manager_task
        for p in reversed(self.patches): p.stop()
        self.engine.dispose()
        self.temp.cleanup()

    @asynccontextmanager
    async def client(self, token=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                headers={"Authorization": f"Bearer {token or self.full}"}) as http:
            async with streamable_http_client("http://127.0.0.1/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    yield client

    async def test_official_client_discovery_and_real_capability_handler(self):
        async with self.client(self.read) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn("baidu_files", names)
            self.assertNotIn("baidu_delete", names)
            self.assertNotIn("mcp_upload_file", names)
            response = await client.call_tool("baidu_capabilities", {})
            self.assertFalse(response.isError)
            self.assertEqual(response.structuredContent["data"]["provider"], "baidu")
            resource = await client.read_resource("xianyu://guide")
            self.assertIn("MCP", resource.contents[0].text)

    async def test_scopes_and_confirmation_block_writes_before_provider(self):
        from app.services import baidu_pcs_drive
        with patch.object(baidu_pcs_drive, "run_operation", AsyncMock()) as external:
            async with self.client(self.read) as client:
                response = await client.call_tool("baidu_delete", {"body": {"paths": ["/file"]}, "confirm": True, "operation_id": "test:" + uuid4().hex})
                self.assertTrue(response.isError)
            async with self.client() as client:
                response = await client.call_tool("baidu_delete", {"body": {"paths": ["/file"]}})
                self.assertTrue(response.isError)
                response = await client.call_tool("baidu_delete", {"body": {"paths": ["/file"]}, "confirm": False})
                self.assertTrue(response.isError)
            external.assert_not_awaited()

    async def test_admin_token_keeps_owner_boundary_in_real_card_route(self):
        async with self.client() as client:
            own = await client.call_tool("cards_get_card", {"path": {"card_id": 1}})
            self.assertFalse(own.isError)
            self.assertIn("own content", json.dumps(own.structuredContent))
            other = await client.call_tool("cards_get_card", {"path": {"card_id": 2}})
            self.assertTrue(other.isError)
            self.assertNotIn("must not leak", json.dumps(other.structuredContent))
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, 1).role, UserRole.ADMIN)

    async def test_private_file_roundtrip_and_cross_owner_rejection(self):
        content = "MCP真实文件传输".encode()
        async with self.client() as client:
            upload = await client.call_tool("mcp_upload_file", {"confirm": True, "operation_id": "test:" + uuid4().hex, "filename": "sample.txt", "content_base64": base64.b64encode(content).decode()})
            self.assertFalse(upload.isError, upload)
            identifier = upload.structuredContent["data"]["upload_id"]
            downloaded = await client.call_tool("mcp_read_file", {"upload_id": identifier, "limit": 5})
            self.assertEqual(base64.b64decode(downloaded.structuredContent["data"]["content_base64"]), content[:5])
            self.assertEqual(downloaded.structuredContent["data"]["next_offset"], 5)
        with self.assertRaises(ValueError): files.lookup(2, identifier)
        with self.assertRaises(ValueError): files.lookup(1, "../outside")
        self.assertEqual((files.owner_dir(1) / identifier / "blob").stat().st_mode & 0o777, 0o600)

    async def test_legacy_card_relations_cannot_cross_owner_boundary(self):
        calls = [
            ("cards_get_card_items", {"path": {"card_id": 2}}),
            ("cards_get_cards_by_item", {"path": {"item_id": "other-item"}}),
            ("cards_update_card_items", {"path": {"card_id": 2}, "body": {"item_ids": ["own-item"]}, "confirm": True, "operation_id": "test:" + uuid4().hex}),
            ("cards_delete_card_item_relation", {"path": {"card_id": 2, "item_id": "other-item"}, "confirm": True, "operation_id": "test:" + uuid4().hex}),
            ("cards_batch_clear_item_relations", {"body": {"item_ids": ["other-item"]}, "confirm": True, "operation_id": "test:" + uuid4().hex}),
            ("cards_batch_bind_cards", {"body": {"card_ids": [2], "item_ids": ["own-item"]}, "confirm": True, "operation_id": "test:" + uuid4().hex}),
        ]
        async with self.client() as client:
            own = await client.call_tool("cards_get_card_items", {"path": {"card_id": 1}})
            self.assertFalse(own.isError, own)
            for name, arguments in calls:
                with self.subTest(tool=name):
                    response = await client.call_tool(name, arguments)
                    self.assertTrue(response.isError)
                    self.assertEqual(response.structuredContent["code"], "INVALID_OPERATION")
                    self.assertNotIn("must not leak", str(response))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(CardItemRelation.item_id)), "other-item")

    async def test_legacy_chat_and_receipt_routes_require_owned_account(self):
        from app.api.routes import chat_new
        with patch.object(chat_new, "get_im_session_manager") as manager:
            async with self.client() as client:
                for tool in ["chat_get_conversations", "confirm_receipt_messages_get_confirm_receipt_message"]:
                    response = await client.call_tool(tool, {"path": {"account_id": "other"}})
                    self.assertTrue(response.isError)
                    self.assertEqual(response.structuredContent["code"], "INVALID_OPERATION")
                response = await client.call_tool("chat_send_message", {
                    "path": {"account_id": "other"},
                    "body": {"cid": "test", "toUserId": "test", "text": "must not send"}, "confirm": True, "operation_id": "test:" + uuid4().hex})
                self.assertEqual(response.structuredContent["code"], "INVALID_OPERATION")
        manager.assert_not_called()

    async def test_owned_item_cannot_expose_foreign_card_relation(self):
        with Session(self.engine) as db:
            db.add(CardItemRelation(user_id=1, card_id=2, item_id="own-item", source="own"))
            db.commit()
        async with self.client() as client:
            response = await client.call_tool("cards_get_cards_by_item", {"path": {"item_id": "own-item"}})
            self.assertTrue(response.isError)
            self.assertEqual(response.structuredContent["code"], "INVALID_OPERATION")
            self.assertNotIn("must not leak", str(response))

    async def test_staged_file_reaches_real_multipart_route(self):
        from app.services import baidu_pcs_drive
        observed = {}
        async def provider(owner, uploads, directory):
            observed.update(owner=owner, directory=directory, data=await uploads[0].read(), name=uploads[0].filename)
            return {"status": "completed", "message": "provider boundary test"}
        staged = await files.store_bytes(1, "sample.txt", b"multipart bytes", "text/plain")
        with patch.object(baidu_pcs_drive, "upload_files", side_effect=provider):
            async with self.client() as client:
                response = await client.call_tool("baidu_upload", {"body": {"files": [{"upload_id": staged["upload_id"]}], "dir": "/isolated"}, "confirm": True, "operation_id": "test:" + uuid4().hex})
                self.assertFalse(response.isError, response)
        self.assertEqual(observed, {"owner": 1, "directory": "/isolated", "data": b"multipart bytes", "name": "sample.txt"})

    async def test_concurrent_file_uploads_respect_owner_quota(self):
        with patch.object(files, "OWNER_QUOTA", 6):
            outcomes = await asyncio.gather(
                files.store_bytes(1, "one.txt", b"1234"),
                files.store_bytes(1, "two.txt", b"5678"), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in outcomes), 1)
        self.assertEqual(sum(p.stat().st_size for p in files.owner_dir(1).glob("*/blob")), 4)

    async def test_file_response_is_preserved_before_original_cleanup(self):
        from app.services import baidu_pcs_drive
        folder = Path(self.temp.name) / "provider-download"
        folder.mkdir()
        source = folder / "sample.txt"
        source.write_bytes(b"download bytes")
        with patch.object(baidu_pcs_drive, "download", AsyncMock(return_value=(source, "sample.txt", folder))):
            async with self.client() as client:
                response = await client.call_tool("baidu_download", {"query": {"path": "/sample.txt"}})
                self.assertFalse(response.isError, response)
                identifier = response.structuredContent["data"]["file"]["upload_id"]
                content = await client.call_tool("mcp_read_file", {"upload_id": identifier})
                self.assertEqual(base64.b64decode(content.structuredContent["data"]["content_base64"]), b"download bytes")
        self.assertFalse(folder.exists())

    async def test_background_job_returns_before_completion(self):
        from app.api.routes import quark_drive
        finish = asyncio.Event()
        started = asyncio.Event()
        async def job(*args):
            started.set()
            await finish.wait()
        with patch.object(quark_drive, "create_job", AsyncMock(return_value={"id": "a" * 32, "status": "queued"})), patch.object(quark_drive, "run_job", side_effect=job):
            try:
                async with self.client() as client:
                    response = await asyncio.wait_for(client.call_tool("quark_create_folder", {"body": {"name": "test", "confirmed": True}, "confirm": True, "operation_id": "test:" + uuid4().hex}), timeout=5)
                    self.assertFalse(response.isError, response)
                    self.assertEqual(response.structuredContent["data"]["job"]["status"], "queued")
                    await asyncio.wait_for(started.wait(), 1)
                    self.assertFalse(finish.is_set())
            finally:
                finish.set()

    async def test_revocation_expiry_and_inactive_owner(self):
        verifier = auth.PersonalTokenVerifier()
        self.assertIsNotNone(await verifier.verify_token(self.full))
        with Session(self.engine) as db:
            db.get(McpAccessToken, self.full_row.id).revoked_at = auth.utcnow()
            db.commit()
        self.assertIsNone(await verifier.verify_token(self.full))
        with Session(self.engine) as db:
            db.get(McpAccessToken, self.read_row.id).expires_at = auth.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertIsNone(await verifier.verify_token(self.read))
        async with Db(self.engine) as db:
            _, token = await auth.issue_token(db, 1, "inactive", ["drive:read"], 1)
        with Session(self.engine) as db:
            db.get(User, 1).status = UserStatus.INACTIVE
            db.commit()
        self.assertIsNone(await verifier.verify_token(token))

    async def test_auth_and_dns_rebinding_protection(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)) as client:
            r = await client.post("http://127.0.0.1/mcp", json={})
            self.assertEqual(r.status_code, 401)
            r = await client.post("http://evil.test/mcp", headers={"Authorization": f"Bearer {self.full}"}, json={})
            self.assertEqual(r.status_code, 421)
            r = await client.post("http://127.0.0.1/mcp", headers={"Authorization": f"Bearer {self.full}", "Origin": "https://evil.test"}, json={})
            self.assertEqual(r.status_code, 403)

    async def test_audit_does_not_store_arguments_or_secrets(self):
        async with self.client() as client:
            await client.call_tool("baidu_capabilities", {})
        with Session(self.engine) as db:
            rows = db.scalars(select(McpToolLog)).all()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].success)
            self.assertNotIn(self.full, str(rows[0].__dict__))

    async def test_search_passes_owner_to_existing_search_service(self):
        from app.api.routes import search
        with patch.object(search, "search_xianyu_items", AsyncMock(return_value={"items": [], "total": 0})) as service:
            async with self.client() as client:
                response = await client.call_tool("search_search_items", {"body": {"keyword": "test"}})
                self.assertFalse(response.isError, response)
        self.assertEqual(service.await_args.kwargs["user_id"], "1")

    async def test_audit_start_failure_prevents_business_execution(self):
        self.gateway.operations.sessions = lambda: Db(self.engine)
        with patch.object(server, "async_session_maker", side_effect=RuntimeError("private database details")), \
                patch.object(self.gateway.bridge, "invoke", AsyncMock()) as business:
            async with self.client() as client:
                response = await client.call_tool("baidu_create_folder", {
                    "body": {"name": "test", "confirmed": True}, "confirm": True, "operation_id": "test:" + uuid4().hex})
        self.assertTrue(response.isError)
        self.assertEqual(response.structuredContent["code"], "AUDIT_UNAVAILABLE")
        self.assertNotIn("private database details", str(response))
        business.assert_not_awaited()

    async def test_audit_completion_failure_preserves_observed_business_result(self):
        self.gateway.operations.sessions = lambda: Db(self.engine)
        with patch.object(server, "async_session_maker", side_effect=[Db(self.engine), RuntimeError("private database details")]), \
                patch.object(self.gateway.bridge, "invoke", AsyncMock(return_value={"success": True, "data": {"created": True}})) as business:
            async with self.client() as client:
                response = await client.call_tool("baidu_create_folder", {
                    "body": {"name": "test", "confirmed": True}, "confirm": True, "operation_id": "test:" + uuid4().hex})
        self.assertFalse(response.isError)
        self.assertTrue(response.structuredContent["data"]["created"])
        self.assertEqual(response.structuredContent["mcp_audit"]["status"], "completion_not_recorded")
        self.assertNotIn("private database details", str(response))
        business.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
