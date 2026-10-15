"""Public package integrity and real database-backed, owner-scoped key management."""
from __future__ import annotations

from datetime import timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend-web"), str(Path(__file__).parent)]

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from test_mcp_gateway import Db
from app.api import api_router, deps
from app.api.routes import ai_connect, mcp_access
from app.mcp import auth, onboarding, server
from common.models.mcp_access import McpAccessToken, McpToolLog
from common.models.user import User, UserRole, UserStatus


class PublicAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = SimpleNamespace(mcp_public_base_url="https://example.test/xianyu-auto-reply", frontend_public_url="")
        self.config = patch.object(onboarding, "get_settings", lambda: self.settings)
        self.config.start()
        app = FastAPI()
        # Match the legacy bootstrap handler; public file errors must bypass it.
        @app.exception_handler(HTTPException)
        async def legacy_error(request, exc):
            return JSONResponse({"success": False, "code": exc.status_code}, status_code=200)
        app.include_router(ai_connect.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://evil.invalid")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.config.stop()

    async def test_anonymous_links_are_canonical_and_package_matches_digest(self):
        info = (await self.client.get("/ai/manifest.json")).json()
        self.assertEqual(info["endpoint"], self.settings.mcp_public_base_url + "/mcp")
        self.assertNotIn("evil.invalid", json.dumps(info))
        guide = await self.client.get("/ai/guide.md")
        self.assertIn(info["skill_sha256"], guide.text)
        response = await self.client.get("/ai/skills/xianyu-operations.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), info["skill_sha256"])
        self.assertEqual(response.content, onboarding.skill_bundle(self.settings.mcp_public_base_url))
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            expected = {*onboarding.SKILL_FILES, "CONNECT.md", "requirements.txt", "config.example.json", "xianyu-mcp.toml", "xianyu-mcp.json"}
            self.assertEqual(names, {"xianyu-operations/" + name for name in expected})
            for name in names:
                self.assertNotIn("..", name.split("/"))
            config = json.loads(archive.read("xianyu-operations/config.example.json"))
            self.assertEqual(config, {"url": info["endpoint"], "token": "<YOUR_MCP_TOKEN>"})
            self.assertIn(info["endpoint"], archive.read("xianyu-operations/agents/openai.yaml").decode())
        cached = await self.client.get("/ai/skills/xianyu-operations.zip", headers={"If-None-Match": response.headers["etag"]})
        self.assertEqual(cached.status_code, 304)
        self.assertFalse(cached.content)

    async def test_only_whitelisted_files_available_and_html_hardened(self):
        for name in onboarding.SKILL_FILES:
            self.assertEqual((await self.client.get("/ai/skills/xianyu-operations/" + name)).status_code, 200)
        for name in [".env", "config.json", "references/../../.env", "data/local-dev/mcp-client.json"]:
            self.assertEqual((await self.client.get("/ai/skills/xianyu-operations/" + name)).status_code, 404)
        landing = await self.client.get("/ai")
        self.assertIn("frame-ancestors 'none'", landing.headers["content-security-policy"])
        self.assertEqual(landing.headers["referrer-policy"], "no-referrer")
        with self.assertRaises(FileNotFoundError): onboarding.skill_file("../.env", self.settings.mcp_public_base_url)

    async def test_config_rejects_unsafe_and_insecure_remote_bases(self):
        for value in ["http://example.test", "https://user:pass@example.test", "https://example.test?key=x", "https://example.test/#key", "https://example.test/%0a", "https://example.test/a/../b", "https://example.test/\"", "javascript:alert(1)"]:
            self.settings.mcp_public_base_url = value
            with self.subTest(value=value), self.assertRaises(ValueError): onboarding.public_base()
        self.settings.mcp_public_base_url = "http://127.0.0.1:9000/"
        self.assertEqual(onboarding.public_base(), "http://127.0.0.1:9000")

    async def test_missing_package_is_real_503_not_successful_download(self):
        with patch.object(onboarding, "SKILL_ROOT", ROOT / "missing-skill-root"):
            for path in ["/ai", "/ai/manifest.json", "/ai/guide.md", "/ai/skills/xianyu-operations.zip"]:
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")


class KeyManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        for model in [User, McpAccessToken, McpToolLog]: model.__table__.create(self.engine)
        with Session(self.engine) as session:
            session.add_all([User(id=identifier, username=f"owner{identifier}", email=f"{identifier}@example.test", password_hash="unused", role=UserRole.MEMBER, status=UserStatus.ACTIVE) for identifier in [1, 2]])
            session.commit()
        self.patch = patch.object(auth, "async_session_maker", lambda: Db(self.engine))
        self.patch.start()
        routes = FastAPI()
        routes.include_router(api_router, prefix="/api/v1")
        self.gateway = server.McpGateway(routes.routes)
        self.app = FastAPI()
        self.app.include_router(mcp_access.router, prefix="/api/v1")
        self.app.state.mcp_gateway = self.gateway
        async def user(): return SimpleNamespace(id=1)
        async def db():
            async with Db(self.engine) as session: yield session
        self.app.dependency_overrides[deps.get_current_active_user] = user
        self.app.dependency_overrides[deps.get_db_session] = db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.gateway.close()
        self.patch.stop()
        self.engine.dispose()

    async def create(self):
        response = await self.client.post("/api/v1/mcp/tokens", json={"name": "independent-client", "scopes": ["drive:read"], "expires_in_days": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()["data"]

    async def test_issue_check_list_audit_revoke_and_hash_only_storage(self):
        data = await self.create()
        secret = data["token"]
        with Session(self.engine) as session:
            row = session.get(McpAccessToken, data["id"])
            self.assertEqual(row.token_hash, hashlib.sha256(secret.encode()).hexdigest())
            session.add_all([McpToolLog(id=f"audit{i}", owner_id=i, token_id=data["id"], tool_name="mcp_capabilities", success=True, duration_ms=3) for i in [1, 2]])
            session.commit()
        checked = await self.client.post("/api/v1/mcp/connection/check", json={"token": secret})
        checked_data = checked.json()["data"]
        self.assertEqual(checked_data["tool_count"], len(self.gateway.metadata(["drive:read"])) + 4)
        self.assertEqual(checked_data["scopes"], ["drive:read"])
        listed = await self.client.get("/api/v1/mcp/tokens")
        self.assertNotIn(secret, listed.text)
        self.assertNotIn("token_hash", listed.text)
        self.assertIsNotNone(listed.json()["data"][0]["last_used_at"])
        logs = (await self.client.get("/api/v1/mcp/logs?page_size=1")).json()
        self.assertEqual(logs["total"], 1)
        self.assertEqual(logs["data"][0]["id"], "audit1")
        self.assertEqual((await self.client.delete("/api/v1/mcp/tokens/" + data["id"])).status_code, 200)
        self.assertIsNone(await auth.PersonalTokenVerifier().verify_token(secret))
        self.assertEqual((await self.client.post("/api/v1/mcp/connection/check", json={"token": secret})).status_code, 400)

    async def test_owner_isolation_expiry_and_invalid_scope(self):
        async with Db(self.engine) as session:
            row, other = await auth.issue_token(session, 2, "other", ["drive:read"], 1)
        self.assertEqual((await self.client.delete("/api/v1/mcp/tokens/" + row.id)).status_code, 404)
        self.assertEqual((await self.client.post("/api/v1/mcp/connection/check", json={"token": other})).status_code, 400)
        self.assertEqual((await self.client.get("/api/v1/mcp/tokens")).json()["data"], [])
        data = await self.create()
        with Session(self.engine) as session:
            session.get(McpAccessToken, data["id"]).expires_at = auth.utcnow() - timedelta(seconds=1)
            session.commit()
        self.assertEqual((await self.client.post("/api/v1/mcp/connection/check", json={"token": data["token"]})).status_code, 400)
        invalid = await self.client.post("/api/v1/mcp/tokens", json={"name": "bad", "scopes": ["admin:write"]})
        self.assertEqual(invalid.status_code, 422)
        self.app.dependency_overrides.pop(deps.get_current_active_user)
        denied = await self.client.get("/api/v1/mcp/tokens")
        self.assertEqual(denied.status_code, 401)


if __name__ == "__main__": unittest.main()
