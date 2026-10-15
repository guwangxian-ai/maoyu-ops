"""Baidu share cancellation references, using the existing real SQLite shim."""
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from test_cloud_assets import AsyncSession
from app.services import baidu_share_guard as guard
from app.services.baidu_drive_auth import BaiduDriveError
from common.models.card import Card
from common.models.cloud_asset import CloudAsset

URL = "https://pan.baidu.com/s/1review-share"


class BaiduShareGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (CloudAsset, Card):
            model.__table__.create(self.engine)
        self.maker = patch.object(guard, "async_session_maker", lambda: AsyncSession(self.engine))
        self.maker.start()

    def tearDown(self):
        self.maker.stop()
        self.engine.dispose()

    def add_asset(self, **overrides):
        values = {"id": "a" * 32, "owner_id": 7, "name": "私有资料名称不应出现在错误", "version": 1,
                  "status": "deliverable", "manifest": [], "targets": [], "published": None}
        values.update(overrides)
        with Session(self.engine) as db:
            db.add(CloudAsset(**values))
            db.commit()

    def add_card(self, **overrides):
        values = {"user_id": 7, "name": "私有卡券名称", "type": "text", "enabled": True,
                  "text_content": "无链接内容", "description": None}
        values.update(overrides)
        with Session(self.engine) as db:
            db.add(Card(**values))
            db.commit()

    @staticmethod
    def target(url=URL, provider="baidu"):
        return {"provider": provider, "account_id": "private-account", "status": "shared", "share_url": url}

    async def test_failed_new_version_still_protects_old_published_link(self):
        published = {"version": 1, "targets": [self.target(URL + "?pwd=a123#download")]}
        self.add_asset(version=2, status="failed", targets=[self.target("https://pan.baidu.com/s/1new-version")],
                       published=deepcopy(published))
        with self.assertRaisesRegex(BaiduDriveError, "已发布版本") as raised:
            await guard.ensure_unreferenced(7, URL)
        self.assertNotIn("私有", str(raised.exception))
        self.assertNotIn(URL, str(raised.exception))
        with Session(self.engine) as db:
            row = db.scalar(select(CloudAsset))
            self.assertEqual(row.published, published)
            self.assertEqual(row.status, "failed")

    async def test_current_target_reference_is_protected_before_publish(self):
        self.add_asset(status="ready", targets=[self.target()])
        with self.assertRaisesRegex(BaiduDriveError, "同步资料"):
            await guard.ensure_unreferenced(7, URL + "?pwd=different")

    async def test_active_baidu_resource_blocks_even_an_unrelated_share(self):
        for status in ("syncing", "sharing", "importing"):
            with self.subTest(status=status):
                with Session(self.engine) as db:
                    db.query(CloudAsset).delete()
                    db.commit()
                self.add_asset(status=status, targets=[self.target("https://pan.baidu.com/s/1unrelated")])
                with self.assertRaisesRegex(BaiduDriveError, "任务正在执行"):
                    await guard.ensure_unreferenced(7, URL)

    async def test_ordinary_card_body_and_description_are_protected(self):
        for field in ("text_content", "description"):
            with self.subTest(field=field):
                with Session(self.engine) as db:
                    db.query(Card).delete()
                    db.commit()
                self.add_card(**{field: f"资料：[{URL}?pwd=a123]（提取码 a123）", "enabled": False})
                with self.assertRaisesRegex(BaiduDriveError, "文字卡券"):
                    await guard.ensure_unreferenced(7, URL)

    async def test_owner_isolation_and_no_references_pass(self):
        self.add_asset(owner_id=8, status="sharing", targets=[self.target()], published={"targets": [self.target()]})
        self.add_card(user_id=8, text_content=URL)
        self.add_asset(id="b" * 32, owner_id=7, status="syncing", targets=[self.target("https://pan.quark.cn/s/1review-share", "quark")])
        self.add_card(text_content="https://pan.baidu.com.evil.invalid/s/1review-share\nhttps://pan.baidu.com/s/1review-share-longer")
        await guard.ensure_unreferenced(7, URL)
        with self.assertRaises(BaiduDriveError):
            await guard.ensure_unreferenced(8, URL)

    async def test_short_link_and_password_page_identify_same_share(self):
        self.add_card(description="下载 pan.baidu.com/share/init?surl=review-share&pwd=abcd。")
        with self.assertRaisesRegex(BaiduDriveError, "文字卡券"):
            await guard.ensure_unreferenced(7, URL)

    async def test_long_link_identity_includes_shareid_and_uk(self):
        self.add_card(text_content="https://pan.baidu.com/share/link?shareid=123&uk=456&pwd=abcd")
        await guard.ensure_unreferenced(7, "https://pan.baidu.com/share/link?shareid=124&uk=456")
        await guard.ensure_unreferenced(7, "https://pan.baidu.com/share/link?shareid=123&uk=789")
        with self.assertRaisesRegex(BaiduDriveError, "文字卡券"):
            await guard.ensure_unreferenced(7, "https://pan.baidu.com:443/share/link?uk=456&shareid=123")

    async def test_database_failure_blocks_without_exposing_provider_detail(self):
        session = AsyncSession(self.engine)
        with patch.object(guard, "async_session_maker", return_value=session), patch.object(
            session, "execute", AsyncMock(side_effect=SQLAlchemyError("secret database connection"))
        ), self.assertRaisesRegex(BaiduDriveError, "已阻止取消") as raised:
            await guard.ensure_unreferenced(7, URL)
        self.assertNotIn("secret", str(raised.exception))

    async def test_unidentifiable_share_and_broken_snapshot_fail_closed(self):
        with self.assertRaisesRegex(BaiduDriveError, "无法识别"):
            await guard.ensure_unreferenced(7, "https://attacker.invalid/s/1review-share")
        self.add_asset(published={"version": 1})
        with self.assertRaisesRegex(BaiduDriveError, "记录无效"):
            await guard.ensure_unreferenced(7, URL)


if __name__ == "__main__":
    unittest.main()
