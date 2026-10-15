"""Connected-chat-account scope and response contracts, with no IM transport.

Load the actual route, active-user dependency and AccountService query bodies
without importing the application's global DB/Redis/IM startup modules. This is
the existing AST-isolation pattern used by test_cloud_asset_delivery.py. SQL is
executed against real in-memory SQLite and requests use a local ASGI transport.
No account credentials, production services or Xianyu users are accessed.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.models import User, UserRole, UserStatus, XYAccount
from common.schemas.common import ApiResponse
from common.utils.auth_scope import is_admin_user


class LocalAsyncSession:
    def __init__(self, session):
        self.session = session
        self.calls = 0
        self.error = None

    async def execute(self, statement):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.session.execute(statement)


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in functions} != set(names):
        raise AssertionError("Required source functions are missing")
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)


class ConnectedChatAccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        XYAccount.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.db = LocalAsyncSession(self.session)
        for pk, owner, account in (
            (1, 7, "own-connected"),
            (2, 7, "own-disconnected"),
            (3, 8, "other-connected"),
        ):
            self.session.add(XYAccount(id=pk, owner_id=owner, account_id=account,
                                       cookie="fixture-not-a-real-cookie", login_method="test"))
        self.session.commit()
        self.manager = MagicMock()
        self.manager.get_connected_account_ids.return_value = ["own-connected", "other-connected", "stale-connected", "own-connected"]
        self.logger = MagicMock()

        async def get_current_user():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        async def get_db_session():
            yield self.db

        self.authenticate = get_current_user
        namespace = {
            "__name__": __name__ + "_isolated_route", "Depends": Depends,
            "HTTPException": HTTPException, "status": status,
            "User": User, "UserStatus": UserStatus, "XYAccount": XYAccount,
            "AsyncSession": AsyncSession, "select": select,
            "ApiResponse": ApiResponse, "is_admin_user": is_admin_user,
            "get_current_user": get_current_user, "get_db_session": get_db_session,
            "get_im_session_manager": lambda: self.manager,
            "logger": self.logger, "router": APIRouter(prefix="/chat-new"),
        }
        load_functions(ROOT / "backend-web/app/api/deps.py", ["get_current_active_user"], namespace)

        service_path = ROOT / "backend-web/app/services/account_service.py"
        service_tree = ast.parse(service_path.read_text(encoding="utf-8-sig"))
        service = next(node for node in service_tree.body if isinstance(node, ast.ClassDef) and node.name == "AccountService")
        methods = [node for node in service.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"__init__", "get_accounts_for_user"}]
        if {node.name for node in methods} != {"__init__", "get_accounts_for_user"}:
            raise AssertionError("Account scope implementation is missing")
        isolated_service = ast.ClassDef(name="AccountService", bases=[], keywords=[], body=methods, decorator_list=[])
        service_module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), isolated_service], type_ignores=[])
        exec(compile(ast.fix_missing_locations(service_module), str(service_path), "exec"), namespace)

        load_functions(ROOT / "backend-web/app/api/routes/chat_new.py", ["list_connected_accounts"], namespace)
        self.app = FastAPI()
        self.app.include_router(namespace["router"])

    async def asyncSetUp(self):
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test.local")

    async def asyncTearDown(self):
        await self.client.aclose()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def login(self, owner=7, role=UserRole.MEMBER, user_status=UserStatus.ACTIVE):
        async def current_user():
            return SimpleNamespace(id=owner, role=role, status=user_status)
        self.app.dependency_overrides[self.authenticate] = current_user

    async def query(self):
        return await self.client.get("/chat-new/connected-accounts")

    async def test_members_and_operators_only_see_their_connected_accounts(self):
        for role in (UserRole.MEMBER, UserRole.OPERATOR):
            with self.subTest(role=role):
                self.login(role=role)
                response = await self.query()
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["success"])
                self.assertEqual(payload["code"], 200)
                self.assertEqual(payload["data"], {"account_ids": ["own-connected"]})
                self.assertNotIn("fixture-not-a-real-cookie", response.text)
                self.assertNotIn("owner_id", response.text)

    async def test_admin_sees_connected_accounts_from_all_owners_without_stale_ids(self):
        self.login(role=UserRole.ADMIN)
        payload = (await self.query()).json()
        self.assertTrue(payload["success"])
        self.assertEqual(set(payload["data"]["account_ids"]), {"own-connected", "other-connected"})
        self.assertEqual(len(payload["data"]["account_ids"]), 2)

    async def test_user_without_matching_accounts_gets_empty_list(self):
        self.login(owner=9)
        payload = (await self.query()).json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"], {"account_ids": []})

    async def test_no_connections_returns_frontend_contract_without_a_database_query(self):
        self.login()
        self.manager.get_connected_account_ids.return_value = []
        payload = (await self.query()).json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], 200)
        self.assertEqual(payload["data"], {"account_ids": []})
        self.assertEqual(self.db.calls, 0)

    async def test_unauthenticated_and_inactive_users_cannot_inspect_connections(self):
        response = await self.query()
        self.assertEqual(response.status_code, 401)
        self.login(user_status=UserStatus.INACTIVE)
        response = await self.query()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.calls, 0)
        self.manager.get_connected_account_ids.assert_not_called()

    async def test_database_error_fails_without_returning_private_exception_details(self):
        self.login()
        self.db.error = SQLAlchemyError("private database connection and account details")
        response = await self.query()
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 40001)
        self.assertIsNone(payload["data"])
        self.assertNotIn("private", response.text)
        self.assertNotIn("private", str(self.logger.error.call_args))


if __name__ == "__main__":
    unittest.main()
