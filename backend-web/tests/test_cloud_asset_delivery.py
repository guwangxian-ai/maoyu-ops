"""Delivery safeguards with an isolated database and the real dispatch function.

No cloud accounts, share endpoints, orders, or message transports are contacted.
"""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.models.cloud_asset import CloudAsset
from common.services.card_delivery_content import build_delivery_content
from common.services.cloud_asset_delivery import (
    CloudAssetDeliveryError,
    render_cloud_asset_delivery,
    resolve_cloud_asset_delivery,
)
from common.services.delivery_utils import process_delivery_content_with_description, replace_order_context_variables


def snapshot():
    return {
        "version": 1, "name": "课程资料", "required_providers": ["quark", "baidu"],
        "delivery_content": "untrusted cached text",
        "targets": [
            {"provider": "quark", "account_id": "quark-connection", "status": "shared",
             "share_url": "https://pan.quark.cn/s/asset-v1", "passcode": "q123", "expires_at": None},
            {"provider": "baidu", "account_id": "baidu-connection", "status": "shared",
             "share_url": "https://pan.baidu.com/s/asset-v1", "passcode": "b123", "expires_at": "2100-01-01T00:00:00Z"},
        ],
    }


def card(**overrides):
    values = dict(id=31, user_id=7, type="text", text_content="stale card text", name="课程卡券",
                  description="", image_url=None, image_urls=None, enabled=True)
    values.update(overrides)
    return SimpleNamespace(**values)


class AsyncReadSession:
    """Exercise actual ORM queries against an in-memory SQLite database."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


class CloudAssetDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        CloudAsset.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.session = AsyncReadSession(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def asset(self, **overrides):
        values = dict(id="a" * 32, owner_id=7, name="课程资料", version=1, status="deliverable",
                      manifest=[], targets=deepcopy(snapshot()["targets"]), published=snapshot(), card_id=31)
        values.update(overrides)
        asset = CloudAsset(**values)
        self.db.add(asset)
        self.db.commit()
        return asset

    async def test_ordinary_card_retains_existing_content_and_description(self):
        ordinary = card(description="订单 {order_id}")
        self.assertIsNone(await resolve_cloud_asset_delivery(self.session, ordinary))
        self.assertEqual(await build_delivery_content(self.session, ordinary, {"order_id": "123"}), "订单 123")

    async def test_multi_platform_uses_validated_snapshot_not_stale_text(self):
        self.asset()
        output = await resolve_cloud_asset_delivery(self.session, card())
        self.assertIn("课程资料（版本 1）", output)
        for target in snapshot()["targets"]:
            self.assertIn(target["share_url"], output)
            self.assertIn(target["passcode"], output)
        self.assertNotIn("cached", output)
        self.assertNotIn("stale", output)

    async def test_description_cannot_drop_cloud_links(self):
        self.asset()
        output = await build_delivery_content(self.session, card(description="订单 {order_id}"), {"order_id": "123"})
        self.assertIn("订单 123", output)
        self.assertIn("https://pan.quark.cn/s/asset-v1", output)
        self.assertIn("https://pan.baidu.com/s/asset-v1", output)

    async def test_expired_platform_stops_all_content_including_images_and_description(self):
        published = snapshot()
        published["targets"][1]["expires_at"] = "2020-01-01T00:00:00Z"
        self.asset(published=published)
        with self.assertRaisesRegex(CloudAssetDeliveryError, "百度网盘分享已过期"):
            await resolve_cloud_asset_delivery(self.session, card())
        self.assertIsNone(await build_delivery_content(
            self.session, card(description="备用描述", image_url="https://example.invalid/image.png")))

    async def test_missing_platform_cannot_fall_back_to_cached_card_text(self):
        published = snapshot()
        published["targets"].pop()
        self.asset(published=published)
        with self.assertRaisesRegex(CloudAssetDeliveryError, "平台不完整"):
            await resolve_cloud_asset_delivery(self.session, card())

    async def test_cross_owner_association_is_rejected_not_treated_as_ordinary(self):
        self.asset(owner_id=8)
        with self.assertRaisesRegex(CloudAssetDeliveryError, "归属不一致"):
            await resolve_cloud_asset_delivery(self.session, card())

    async def test_failed_new_version_keeps_last_valid_published_version(self):
        self.asset(version=2, status="failed", name="新课程", targets=[{"provider": "quark", "status": "failed"}])
        output = await resolve_cloud_asset_delivery(self.session, card())
        self.assertIn("版本 1", output)
        self.assertNotIn("新课程", output)
        self.assertIn("pan.baidu.com", output)

    async def test_unpublished_status_alone_does_not_allow_delivery(self):
        self.asset(published=None, status="deliverable")
        with self.assertRaisesRegex(CloudAssetDeliveryError, "尚未发布"):
            await resolve_cloud_asset_delivery(self.session, card())

    async def test_database_failure_is_not_treated_as_an_unlinked_card(self):
        with patch.object(self.session, "execute", AsyncMock(side_effect=SQLAlchemyError("private database detail"))):
            with self.assertRaisesRegex(CloudAssetDeliveryError, "关联查询失败") as error:
                await resolve_cloud_asset_delivery(self.session, card())
        self.assertNotIn("private", str(error.exception))

    def test_expiration_boundary_and_permanent_link(self):
        published = snapshot()
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        published["targets"][1]["expires_at"] = now.isoformat()
        with self.assertRaisesRegex(CloudAssetDeliveryError, "已过期"):
            render_cloud_asset_delivery(published, now=now)
        published["targets"][1]["expires_at"] = None
        self.assertIn("pan.baidu.com", render_cloud_asset_delivery(published, now=now))

    def test_invalid_url_account_status_and_metadata_fail_closed(self):
        cases = [
            {"share_url": "http://pan.quark.cn/s/test"},
            {"share_url": "https://pan.baidu.com/s/wrong-provider"},
            {"share_url": "https://pan.quark.cn.evil.invalid/s/test"},
            {"share_url": "https://user:secret@pan.quark.cn/s/test"},
            {"share_url": "https://pan.quark.cn:8443/s/test"},
            {"share_url": "https://pan.quark.cn/s/"},
            {"status": "uploaded"}, {"account_id": ""},
            {"expires_at": "not-a-date"}, {"expires_at": ""}, {"passcode": "a\n12"},
        ]
        for change in cases:
            with self.subTest(change=change):
                published = snapshot()
                published["targets"][0].update(change)
                with self.assertRaises(CloudAssetDeliveryError) as error:
                    render_cloud_asset_delivery(published)
                self.assertNotIn("https://", str(error.exception))
                self.assertNotIn("secret", str(error.exception))
        for change in ({"required_providers": []}, {"required_providers": ["quark", "quark"]}, {"version": True}):
            with self.subTest(change=change), self.assertRaises(CloudAssetDeliveryError):
                render_cloud_asset_delivery({**snapshot(), **change})


class WebsocketDeliveryGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Load the real method body without starting the websocket application's
        # Redis, database compatibility layer, or other process-wide services.
        path = ROOT / "websocket/app/services/xianyu/auto_delivery_handler.py"
        tree = ast.parse(path.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AutoDeliveryHandler")
        method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_auto_delivery")
        self.resolver = AsyncMock()
        namespace = {
            "logger": MagicMock(), "asyncio": asyncio, "time": time,
            "CloudAssetDeliveryError": CloudAssetDeliveryError, "resolve_cloud_asset_delivery": self.resolver,
            "process_delivery_content_with_description": process_delivery_content_with_description,
            "_replace_order_context_variables": replace_order_context_variables,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        self.dispatch = namespace["_auto_delivery"]
        self.handler = SimpleNamespace(
            cookie_id="test-cookie", _last_delivery_fail_reason=None, _safe_str=str,
            is_only_send_card_enabled=lambda: False, is_auto_confirm_enabled=lambda: True,
            auto_confirm=AsyncMock(return_value={"success": True}), confirmed_orders={}, order_confirm_cooldown=60,
            _resolve_buyer_name_for_variable=AsyncMock(return_value="buyer"),
        )
        self.db = MagicMock()
        self.db.get_item_multi_spec_status.return_value = False
        self.db.get_cards_by_item_id.return_value = [vars(card())]
        self.db.get_cookie_by_id.return_value = {"remark": "seller"}
        self.db.get_order_by_id.return_value = {"order_id": "order-1"}
        self.db.get_item_info.return_value = {"title": "资料"}
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=object())
        context.__aexit__ = AsyncMock(return_value=False)
        self.modules = patch.dict(sys.modules, {
            "common.db.compat": SimpleNamespace(db_manager=self.db),
            "common.db.session": SimpleNamespace(async_session_maker=lambda: context),
        })
        self.modules.start()

    def tearDown(self):
        self.modules.stop()

    async def test_invalid_asset_never_confirms_shipping_or_counts_delivery(self):
        for use_form in (False, True):
            with self.subTest(use_form=use_form):
                self.db.get_cards_by_item_id.return_value[0]["use_no_logistics_form"] = use_form
                self.resolver.side_effect = CloudAssetDeliveryError("百度网盘分享已过期")
                self.assertIsNone(await self.dispatch(self.handler, "item-1", order_id="order-1"))
                self.handler.auto_confirm.assert_not_awaited()
                self.db.increment_delivery_count.assert_not_called()
                self.assertIn("分享已过期", self.handler._last_delivery_fail_reason)

    async def test_valid_asset_replaces_cached_text_in_actual_text_branch(self):
        self.resolver.return_value = render_cloud_asset_delivery(snapshot())
        self.db.get_cards_by_item_id.return_value[0]["description"] = "订单 {order_id}"
        result = await self.dispatch(self.handler, "item-1", order_id="order-1", skip_confirm=True)
        self.assertIn("订单 order-1", result["content"])
        self.assertIn("pan.baidu.com", result["content"])
        self.assertIn("pan.quark.cn", result["content"])
        self.assertNotIn("stale", result["content"])
        self.db.increment_delivery_count.assert_called_once_with(31)

    async def test_ordinary_websocket_text_remains_unchanged(self):
        self.resolver.return_value = None
        result = await self.dispatch(self.handler, "item-1", order_id="order-1", skip_confirm=True)
        self.assertEqual(result["content"], "stale card text")
        self.db.increment_delivery_count.assert_called_once_with(31)

    async def test_form_shipping_receives_published_content_before_confirmation(self):
        expected = render_cloud_asset_delivery(snapshot())
        self.resolver.return_value = expected
        self.db.get_cards_by_item_id.return_value[0]["use_no_logistics_form"] = True
        result = await self.dispatch(self.handler, "item-1", order_id="order-1")
        self.handler.auto_confirm.assert_awaited_once_with("order-1", "item-1", trade_text=expected)
        self.assertEqual(result["content"], expected)
        self.assertTrue(result["platform_shipping_confirmed"])


if __name__ == "__main__":
    unittest.main()
