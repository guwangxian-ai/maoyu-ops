"""Real isolated ORM + fake platform: polishing must preserve operating records."""
from datetime import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select, update
from test_mcp_gateway import Db
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.default_reply import DefaultReply
from common.models.scheduled_polish_log import ScheduledPolishLog
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.models.xy_order import XYOrder
from common.services.item_service import ItemService

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "polish_task_under_test", ROOT / "scheduler/app/services/scheduler/polish_task.py")
polish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(polish)


class PolishTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        for model in (XYAccount, XYCatalogItem, Card, CardItemRelation, DefaultReply, XYOrder, ScheduledPolishLog):
            model.__table__.create(self.engine)
        self.db = Db(self.engine)
        self.account = XYAccount(id=1, owner_id=1, account_id="shop", cookie="unb=shop;",
                                 login_method="cookie", auto_polish=True, status="active")
        self.db.add(self.account)
        for identifier, status, account_pk in (("live", "on_sale", 1), ("pending", "pending_verification", 1),
                                                ("unchecked", None, 1), ("foreign", "on_sale", 2)):
            metadata = {"description": "保留资料说明", "multi_quantity_delivery": True}
            if status:
                metadata["sale_snapshot"] = {"status": status, "sync_id": "before",
                    "checked_at": "2026-10-01T00:00:00+00:00", "last_seen_at": "2026-10-01T00:00:00+00:00"}
            self.db.add(XYCatalogItem(owner_id=account_pk, account_pk=account_pk, item_id=identifier,
                title=identifier, price="3", ai_prompt="保留专属话术", is_polished=False,
                metadata_json=metadata, created_at=datetime(2026, 1, 1)))
        self.db.add(Card(id=1, user_id=1, name="交付资料", type="text", enabled=True, text_content="原卡券"))
        self.db.add(CardItemRelation(user_id=1, card_id=1, item_id="live"))
        self.db.add(DefaultReply(account_id="shop", item_id="live", enabled=True, reply_content="付款后发货"))
        self.db.add(XYOrder(owner_id=1, order_no="history", account_id="shop", item_id="live", status="completed"))
        await self.db.commit()
        self.service = polish.PolishTaskService()
        self.service._polish_item = AsyncMock()
        self.sleep = patch.object(polish.asyncio, "sleep", AsyncMock())
        self.sleep.start()

    async def asyncTearDown(self):
        self.sleep.stop()
        self.db.db.close()
        self.engine.dispose()

    def row(self, identifier="live"):
        return self.db.db.scalar(select(XYCatalogItem).where(XYCatalogItem.item_id == identifier))

    async def run_result(self, result):
        self.service._polish_item.return_value = result
        return await self.service._process_account(self.db, self.account, "test-batch")

    def assert_preserved(self):
        self.assertEqual(len(self.db.db.scalars(select(XYCatalogItem)).all()), 4)
        self.assertEqual(self.row().ai_prompt, "保留专属话术")
        self.assertEqual(self.row().metadata_json["description"], "保留资料说明")
        self.assertTrue(self.row().metadata_json["multi_quantity_delivery"])
        self.assertEqual(self.db.db.scalar(select(Card)).text_content, "原卡券")
        self.assertEqual(self.db.db.scalar(select(CardItemRelation)).item_id, "live")
        self.assertEqual(self.db.db.scalar(select(DefaultReply)).reply_content, "付款后发货")
        self.assertEqual(self.db.db.scalar(select(XYOrder)).status, "completed")

    async def test_only_confirmed_on_sale_and_current_account_are_polished(self):
        self.assertEqual(await self.run_result({"success": True, "message": "ok"}), (1, 0))
        self.service._polish_item.assert_awaited_once_with("unb=shop;", "live")
        self.assertTrue(self.row().is_polished)
        self.assertFalse(self.row("foreign").is_polished)
        self.assert_preserved()

    async def test_unsupported_marks_pending_preserving_all_business_data(self):
        before = self.row().metadata_json["sale_snapshot"].copy()
        identity = self.row().id
        self.assertEqual(await self.run_result({"success": False, "message": "UNSUPPORTED_ITEM_STATUS::拒绝"}), (0, 1))
        self.assertEqual(self.row().id, identity)
        snapshot = self.row().metadata_json["sale_snapshot"]
        self.assertEqual(snapshot["status"], "pending_verification")
        for key in ("checked_at", "last_seen_at", "sync_id"):
            self.assertEqual(snapshot[key], before[key])
        self.assert_preserved()
        self.assertEqual(await self.service._get_unpolished_items(self.db, 1), [])
        serialized = ItemService(self.db)._serialize_item(self.row(), "shop")
        self.assertEqual(serialized["sale_verification_reason"], "polish_unsupported_status")
        self.assertEqual(serialized["polish_status"], "unsupported_status")
        self.assertIsNotNone(serialized["polish_checked_at"])

    async def test_chinese_offline_response_keeps_record(self):
        await self.run_result({"success": False, "message": "已下架商品不支持该操作"})
        self.assertEqual(self.row().metadata_json["sale_snapshot"]["status"], "pending_verification")
        self.assert_preserved()

    async def test_transient_failure_does_not_change_sale_snapshot(self):
        before = self.row().metadata_json["sale_snapshot"].copy()
        await self.run_result({"success": False, "message": "网络请求失败"})
        self.assertEqual(self.row().metadata_json["sale_snapshot"], before)
        self.assertFalse(self.row().is_polished)
        self.assertEqual(self.row().metadata_json["polish_check"]["status"], "failed")
        self.assert_preserved()

    async def test_duplicate_is_a_confirmed_success_and_not_retried(self):
        await self.run_result({"success": False, "message": "POLISH_DUPLICATE::一天只能擦亮一次"})
        self.assertTrue(self.row().is_polished)
        self.assertEqual(await self.service._get_unpolished_items(self.db, 1), [])
        self.assertEqual(self.db.db.scalar(select(ScheduledPolishLog)).status, "success")

    async def test_session_failure_preserves_sale_status_and_stops_account(self):
        with patch("common.utils.cookie_refresh.mark_account_session_expired") as cool, \
             patch("common.utils.cookie_refresh.trigger_password_login_async") as login:
            await self.run_result({"success": False, "message": "SESSION_EXPIRED"})
        cool.assert_called_once_with("shop")
        login.assert_called_once_with("shop")
        self.assertEqual(self.row().metadata_json["sale_snapshot"]["status"], "on_sale")
        self.assert_preserved()

    async def test_refresh_during_request_preserves_new_snapshot_and_settings(self):
        async def platform(*_):
            latest = {"description": "new settings", "sale_snapshot": {"status": "on_sale", "sync_id": "new"}}
            self.db.db.execute(update(XYCatalogItem).where(XYCatalogItem.item_id == "live")
                .values(metadata_json=latest, ai_prompt="new prompt").execution_options(synchronize_session=False))
            return {"success": False, "message": "UNSUPPORTED_ITEM_STATUS"}
        self.service._polish_item.side_effect = platform
        await self.service._process_account(self.db, self.account, "race")
        self.assertEqual(self.row().metadata_json["sale_snapshot"], {"status": "on_sale", "sync_id": "new"})
        self.assertEqual(self.row().metadata_json["description"], "new settings")
        self.assertEqual(self.row().ai_prompt, "new prompt")

    async def test_pending_after_selection_is_skipped_before_platform_call(self):
        original = self.service._get_unpolished_items
        async def select_then_change(*args):
            rows = await original(*args)
            self.db.db.execute(update(XYCatalogItem).where(XYCatalogItem.item_id == "live")
                .values(metadata_json={"sale_snapshot": {"status": "pending_verification"}})
                .execution_options(synchronize_session=False))
            return rows
        self.service._get_unpolished_items = select_then_change
        self.assertEqual(await self.service._process_account(self.db, self.account, "race"), (0, 0))
        self.service._polish_item.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
