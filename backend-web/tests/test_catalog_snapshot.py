"""Catalog lifecycle regression: real isolated ORM, fake provider; never use production data."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend-web"), str(Path(__file__).parent)]

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from test_mcp_gateway import Db
from app.api import deps
from app.api.routes.items import items_router
from app.mcp.catalog import build_catalog
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.default_reply import DefaultReply
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.xy_order import XYOrder
from common.services.item_service import ItemService
from common.services.xianyu_seller_item_client import SellerItemInfoManager
from common.utils.item_info_manager import ItemInfoManager


def item(identifier, title=None):
    return {"id": identifier, "title": title or identifier, "price_text": "0.10", "category_id": "",
            "item_status_desc": "在卖", "source": "seller", "quantity": 999999}


def page(*ids, more=False):
    return {"success": True, "items": [item(i) for i in ids], "has_more": more}


class CatalogSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        for model in [XYAccount, XYCatalogItem, Card, CardItemRelation, DefaultReply, XYOrder]:
            model.__table__.create(self.engine)
        self.db = Db(self.engine)
        self.account = XYAccount(id=1, owner_id=1, account_id="shop1", cookie="unb=shop1;", login_method="cookie")
        self.other = XYAccount(id=2, owner_id=2, account_id="shop2", cookie="unb=shop2;", login_method="cookie")
        self.db.db.add_all([self.account, self.other])
        self.old_check = "2026-10-01T00:00:00+00:00"
        for key, account in [("live", self.account), ("missing", self.account), ("other", self.other)]:
            self.db.add(XYCatalogItem(owner_id=account.owner_id, account_pk=account.id, item_id=key, title=key,
                price="0.10", ai_prompt="保留专属 AI 话术", created_at=datetime(2026, 1, 1),
                metadata_json={"detail": json.dumps(item(key), ensure_ascii=False), "category": "",
                    "multi_quantity_delivery": True, "description": "保留资料说明",
                    "sale_snapshot": {"status": "on_sale", "checked_at": self.old_check,
                        "last_seen_at": self.old_check, "sync_id": "old"}}))
        self.db.add(CardItemRelation(user_id=1, card_id=1, item_id="missing"))
        self.db.add(DefaultReply(account_id="shop1", item_id="missing", enabled=True, reply_content="付款后发货"))
        self.db.add(XYOrder(owner_id=1, order_no="old-order", account_id="shop1", item_id="missing", status="completed"))
        await self.db.commit()
        self.service = ItemService(self.db)
        self.manager = SimpleNamespace(get_item_list_info=AsyncMock(), close=AsyncMock())
        self.resolve = patch.object(self.service, "_resolve_item_fetch_manager", AsyncMock(return_value=self.manager))
        self.resolve.start()
        self.sleep = patch("common.services.item_service.asyncio.sleep", new=AsyncMock())
        self.sleep.start()

    async def asyncTearDown(self):
        self.sleep.stop()
        self.resolve.stop()
        self.db.db.close()
        self.engine.dispose()

    def row(self, key):
        return self.db.db.execute(select(XYCatalogItem).where(XYCatalogItem.item_id == key)).scalar_one()

    def status(self, key):
        return self.row(key).metadata_json["sale_snapshot"]

    async def fetch(self, *pages, **kwargs):
        self.manager.get_item_list_info.side_effect = list(pages)
        return await self.service._fetch_all_items_from_account_impl(self.account, **kwargs)

    async def test_full_snapshot_preserves_bindings_ai_and_history(self):
        old_id = self.row("missing").id
        result = await self.fetch(page("live", "new"))
        self.assertTrue(result["snapshot_complete"] and result["state_updated"])
        self.assertEqual((result["on_sale_count"], result["pending_count"]), (2, 1))
        self.assertEqual(self.status("missing")["status"], "pending_verification")
        self.assertEqual(self.status("missing")["last_seen_at"], self.old_check)
        self.assertEqual(self.status("missing")["checked_at"], result["checked_at"])
        self.assertEqual(self.status("other")["sync_id"], "old")
        self.assertEqual(self.row("missing").id, old_id)
        self.assertEqual(self.row("missing").ai_prompt, "保留专属 AI 话术")
        self.assertTrue(self.row("missing").metadata_json["multi_quantity_delivery"])
        self.assertEqual(self.db.db.scalar(select(CardItemRelation)).item_id, "missing")
        self.assertEqual(self.db.db.scalar(select(DefaultReply)).reply_content, "付款后发货")
        self.assertEqual(self.db.db.scalar(select(XYOrder)).status, "completed")

    async def test_second_page_failure_commits_nothing(self):
        result = await self.fetch(page("new", more=True), {"success": False, "message": "provider error"})
        self.assertFalse(result["success"])
        self.assertEqual(self.status("missing")["sync_id"], "old")
        self.assertIsNone(self.db.db.scalar(select(XYCatalogItem).where(XYCatalogItem.item_id == "new")))

    async def test_commit_failure_rolls_back_whole_snapshot(self):
        with patch.object(self.db, "commit", AsyncMock(side_effect=RuntimeError("db failed"))):
            result = await self.fetch(page("live", "new"))
        self.assertFalse(result["success"])
        self.assertEqual(self.status("live")["checked_at"], self.old_check)
        self.assertEqual(self.status("missing")["status"], "on_sale")

    async def test_valid_empty_list_marks_history_pending_without_deletion(self):
        result = await self.fetch(page())
        self.assertTrue(result["snapshot_complete"])
        self.assertEqual(result["pending_count"], 2)
        self.assertEqual(self.status("missing")["status"], "pending_verification")

    async def test_unchanged_list_advances_check_time_and_reappearance_restores_status(self):
        await self.fetch(page())
        result = await self.fetch(page("live", "missing"))
        self.assertEqual(self.status("missing")["status"], "on_sale")
        self.assertEqual(self.status("missing")["last_seen_at"], result["checked_at"])
        self.assertNotEqual(result["checked_at"], self.old_check)

    async def test_page_limit_does_not_update_snapshot(self):
        result = await self.fetch(page("live", more=True), max_pages=1)
        self.assertTrue(result["success"])
        self.assertFalse(result["snapshot_complete"] or result["state_updated"])
        self.assertEqual(self.status("live")["checked_at"], self.old_check)
        self.assertEqual(self.status("missing")["status"], "on_sale")

    async def test_explicit_end_on_page_limit_is_complete(self):
        result = await self.fetch(page("live"), max_pages=1)
        self.assertTrue(result["snapshot_complete"])

    async def test_short_page_with_more_is_not_end(self):
        result = await self.fetch(page("live", more=True), page("missing"), page_size=20)
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(result["on_sale_count"], 2)

    async def test_incremental_stop_keeps_old_snapshot(self):
        result = await self.fetch(page("live", more=True), stop_when_page_all_existing=True)
        self.assertEqual(result["termination_reason"], "existing_page")
        self.assertFalse(result["snapshot_complete"])
        self.assertEqual(self.status("missing")["sync_id"], "old")

    async def test_malformed_duplicate_and_contradictory_pages_never_reconcile(self):
        cases = [({"success": True},), (page("live", "live"),),
                 (page(""),), (page(more=True),),
                 (page("live", more=True), page("live")),
                 ({"success": True, "items": [None]},)]
        for pages in cases:
            with self.subTest(pages=pages):
                result = await self.fetch(*pages)
                self.assertFalse(result["success"])
                self.assertEqual(self.status("missing")["sync_id"], "old")

    async def test_lost_lease_before_commit_rolls_back(self):
        result = await self.fetch(page("live"), lease_check=AsyncMock(side_effect=[True, False]))
        self.assertFalse(result["success"])
        self.assertEqual(self.status("missing")["sync_id"], "old")

    async def test_lock_skipped_and_unavailable_do_not_fetch(self):
        @asynccontextmanager
        async def busy(*args, **kwargs):
            yield SimpleNamespace(is_locked=False)
        with patch("common.services.item_service.distributed_lock", busy):
            result = await self.service.fetch_all_items_from_account(self.account)
        self.assertTrue(result["skipped"])
        self.assertFalse(result["state_updated"])
        self.manager.get_item_list_info.assert_not_awaited()
        @asynccontextmanager
        async def broken(*args, **kwargs):
            raise RuntimeError("redis unavailable")
            yield
        with patch("common.services.item_service.distributed_lock", broken):
            result = await self.service.fetch_all_items_from_account(self.account)
        self.assertFalse(result["success"])
        self.manager.get_item_list_info.assert_not_awaited()
        self.assertEqual(self.status("live")["sync_id"], "old")

    async def test_new_local_item_during_fetch_is_not_marked_missing(self):
        self.db.add(XYCatalogItem(owner_id=1, account_pk=1, item_id="just-created", title="new", ai_prompt="keep",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=10), metadata_json={}))
        await self.db.commit()
        await self.fetch(page("live"))
        self.assertNotIn("sale_snapshot", self.row("just-created").metadata_json)

    async def test_single_page_does_not_change_snapshot(self):
        self.manager.get_item_list_info.return_value = page("live")
        result = await self.service.fetch_items_page_from_account(self.account)
        self.assertFalse(result["snapshot_complete"])
        self.assertEqual(self.status("missing")["sync_id"], "old")

    async def test_batch_reports_skips_instead_of_empty_shop(self):
        with patch.object(self.service, "fetch_all_items_from_account", AsyncMock(side_effect=[
            {"success": True, "snapshot_complete": True, "state_updated": True, "total_count": 1},
            {"success": True, "skipped": True, "snapshot_complete": False, "state_updated": False},
        ])):
            result = await self.service.fetch_all_items_from_accounts([self.account, self.other])
        self.assertEqual(result["complete_account_count"], 1)
        self.assertEqual(result["skipped_account_count"], 1)
        self.assertFalse(result["snapshot_complete"])
        self.assertTrue(result["results"][1]["skipped"])

    async def test_batch_failure_isolated_to_its_account(self):
        @asynccontextmanager
        async def lock(*args, **kwargs):
            yield SimpleNamespace(is_locked=True, extend=AsyncMock(return_value=True))
        self.manager.get_item_list_info.side_effect = [page("live"), {"success": False, "message": "offline"}]
        with patch("common.services.item_service.distributed_lock", lock):
            result = await self.service.fetch_all_items_from_accounts([self.account, self.other])
        self.assertEqual(result["complete_account_count"], 1)
        self.assertEqual(result["failed_account_count"], 1)
        self.assertEqual(self.status("missing")["status"], "pending_verification")
        self.assertEqual(self.status("other")["sync_id"], "old")

    async def test_filter_counts_pagination_and_owner_scope(self):
        await self.fetch(page("live"))
        self.db.add(XYCatalogItem(owner_id=1, account_pk=1, item_id="legacy", title="legacy",
            created_at=datetime(2026, 1, 1), metadata_json={"detail": '{"item_status_desc":"在卖"}'}))
        await self.db.commit()
        for status, expected in [("on_sale", "live"), ("pending_verification", "missing"), ("unchecked", "legacy")]:
            rows, total = await self.service.list_items_paginated(1, "shop1", page_size=1, sale_status=status)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["item_id"], expected)
            self.assertEqual(rows[0]["sale_status"], status)
        rows, total = await self.service.list_items_paginated(1, sale_status="pending_verification", keyword="missing")
        self.assertEqual(total, 1)
        self.assertTrue(rows[0]["has_card"] and rows[0]["has_ai_prompt"] and rows[0]["has_default_reply"])

    async def test_api_filter_and_mcp_schema(self):
        app = FastAPI()
        app.include_router(items_router, prefix="/api/v1")
        app.dependency_overrides[deps.get_current_active_user] = lambda: SimpleNamespace(id=1, role="member")
        app.dependency_overrides[deps.get_item_service] = lambda: self.service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/items/paginated", params={"sale_status": "on_sale", "cookie_id": "shop1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 2)
            invalid = await client.get("/api/v1/items/paginated?sale_status=guess_offline")
            self.assertEqual(invalid.status_code, 422)
        schema = build_catalog(app.routes)["items_list_items_paginated"].schema
        self.assertIn("pending_verification", json.dumps(schema))


class ProviderParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_seller_missing_list_or_business_error_not_valid_empty(self):
        manager = SellerItemInfoManager("test", "unused")
        for data in [{}, {"data": {}}, {"data": {"itemSearchResponseList": []}},
                     {"code": "error", "data": {"itemSearchResponseList": [], "hasNextPage": False}},
                     {"data": {"itemSearchResponseList": [None], "hasNextPage": False}}]:
            with patch("common.services.xianyu_seller_item_client.mtop_call", AsyncMock(return_value={"success": True, "res": {"data": data}})):
                self.assertFalse((await manager.get_item_list_info(1, 20))["success"])

    async def test_seller_explicit_empty_and_false_string(self):
        manager = SellerItemInfoManager("test", "unused")
        for more in [False, "false"]:
            with patch("common.services.xianyu_seller_item_client.mtop_call", AsyncMock(return_value={"success": True,
                "res": {"data": {"code": "success", "data": {"itemSearchResponseList": [], "hasNextPage": more}}}})):
                result = await manager.get_item_list_info(1, 20)
                self.assertTrue(result["success"])
                self.assertFalse(result["has_more"])

    async def test_personal_missing_card_list_not_empty(self):
        @asynccontextmanager
        async def post(*args, **kwargs):
            yield SimpleNamespace(headers={}, json=AsyncMock(return_value={"ret": ["SUCCESS::调用成功"], "data": {}}))
        manager = ItemInfoManager("test", "unused", session=SimpleNamespace(post=post))
        result = await manager.get_item_list_info(1, 20)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
