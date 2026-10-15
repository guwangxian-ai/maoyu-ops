"""Native API contracts; provider fixtures never enter production adapters."""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import baidu_pcs_auth as auth, baidu_pcs_cli as cli, baidu_pcs_drive as drive
from app.services.baidu_drive_auth import BaiduDriveError, _locks, read_private, write_private

ROW = {"fs_id": "9007199254740993", "path": "/原有资料/说明.txt", "filename": "说明.txt",
       "size": 12, "isdir": False, "server_mtime": 1759478400, "md5": "a" * 32}
RECORD = {"account_id": "a" * 32, "nickname": "合同测试账号"}


class PcsOperations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BAIDU_AUTH_DIR": self.temporary.name})
        self.environment.start()
        self.authorization = patch.object(auth, "require_account", AsyncMock(return_value=RECORD))
        self.authorization.start()
        self.delivery_guard = patch.object(drive, "ensure_unreferenced", AsyncMock())
        self.delivery_guard.start()
        _locks.clear()

    def tearDown(self):
        _locks.clear()
        self.authorization.stop()
        self.delivery_guard.stop()
        self.environment.stop()
        self.temporary.cleanup()

    async def test_original_paths_and_root_restrictions(self):
        self.assertEqual(drive.validate_path("/"), "/")
        self.assertEqual(drive.validate_path(ROW["path"]), ROW["path"])
        self.assertEqual(drive.validate_path("/apps/bdpan/旧资料.txt"), "/apps/bdpan/旧资料.txt")
        for value in ("/", "/../文档", "/资料/../文档", "/资料//文档", "相对目录", "/资料\\文档", "/资料\n文档"):
            with self.subTest(value=value), self.assertRaises(BaiduDriveError):
                drive.validate_path(value, allow_root=False)

    async def test_list_preserves_large_ids_and_browses_real_root(self):
        with patch.object(cli, "run", AsyncMock(return_value={"files": [ROW], "has_more": True})) as call:
            result = await drive.list_files(17, "/", page=2)
        self.assertEqual(result["files"][0]["fs_id"], "9007199254740993")
        self.assertTrue(result["has_more"])
        self.assertEqual(call.call_args.args, (17, "list", {"dir": "/", "page": 2, "page_size": 100}))

    async def test_concurrent_page_reads_queue_without_overlapping_provider_calls(self):
        active = 0
        maximum_active = 0
        async def provider(owner, command, payload, **kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"files": [ROW], "has_more": False} if command == "list" else {"shares": [], "has_more": False}
        with patch.object(cli, "run", AsyncMock(side_effect=provider)):
            files, shares = await asyncio.gather(drive.list_files(17), drive.list_shares(17))
        self.assertEqual(maximum_active, 1)
        self.assertEqual(files["files"][0]["fs_id"], ROW["fs_id"])
        self.assertEqual(shares["scope"], "account")

    async def test_writes_require_explicit_confirmation(self):
        with patch.object(cli, "run", AsyncMock()) as call:
            for operation in ("create-folder", "delete", "copy", "move", "rename", "upload-text"):
                with self.subTest(operation=operation), self.assertRaises(BaiduDriveError):
                    await drive.run_operation(17, operation, {"paths": [ROW["path"]]})
            call.assert_not_awaited()

    async def test_stale_id_cannot_operate_on_changed_path(self):
        with patch.object(cli, "run", AsyncMock(return_value={"files": [{**ROW, "fs_id": "12"}]})) as call:
            with self.assertRaisesRegex(BaiduDriveError, "不一致"):
                await drive.create_share(17, [ROW["fs_id"]], "说明", paths=[ROW["path"]])
            self.assertEqual(call.await_count, 1)
            self.assertEqual(call.call_args.args[1], "meta")

    async def test_foreign_account_cached_id_is_not_reused(self):
        write_private(drive._records_path(17, "b" * 32, "files.json"), {ROW["fs_id"]: ROW["path"]})
        with patch.object(cli, "run", AsyncMock()) as call:
            with self.assertRaisesRegex(BaiduDriveError, "先查询"):
                await drive.create_share(17, [ROW["fs_id"]], "说明")
            call.assert_not_awaited()

    async def test_copy_collision_stops_before_mutation(self):
        with patch.object(drive, "_resolve", AsyncMock(return_value=[ROW])), patch.object(drive, "_directory", AsyncMock(return_value=[ROW])), patch.object(cli, "run", AsyncMock()) as call:
            result = await drive.run_operation(17, "copy", {"paths": [ROW["path"]], "target_dir": "/", "confirmed": True})
            self.assertEqual(result["status"], "failed")
            call.assert_not_awaited()

    async def test_share_uses_native_id_password_and_private_record(self):
        async def provider(owner, command, payload, **kwargs):
            if command == "meta":
                return {"files": [ROW]}
            self.assertEqual(command, "share-create")
            self.assertEqual(payload, {"paths": [ROW["path"]], "password": "a123", "period": 1})
            return {"id": "2345", "share_url": "https://pan.baidu.com/s/contract", "passcode": "a123"}
        with patch.object(cli, "run", AsyncMock(side_effect=provider)):
            result = await drive.create_share(17, [], "说明", 1, "a123", [ROW["path"]])
        self.assertEqual(result["share"]["id"], "2345")
        self.assertEqual(result["share"]["status"], "active")
        self.assertGreater(result["share"]["expires_at"], 0)
        path = drive._records_path(17, RECORD["account_id"], "shares.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(read_private(path)[0]["passcode"], "a123")

    async def test_unknown_share_creation_time_is_not_fabricated(self):
        raw = {"shareId": "2345", "fsIds": [ROW["fs_id"]], "shortlink": "https://pan.baidu.com/s/contract",
               "typicalPath": ROW["path"], "public": 0, "pwd": "a123", "expiredType": 1, "expiredTime": 100}
        with patch.object(cli, "run", AsyncMock(return_value={"shares": [raw], "has_more": True})) as call:
            result = await drive.list_shares(17, 2)
        self.assertEqual(result["scope"], "account")
        self.assertTrue(result["has_more"])
        self.assertIsNone(result["shares"][0]["created_at"])
        self.assertEqual(result["shares"][0]["period"], -1)
        self.assertEqual(call.call_args.args, (17, "share-list", {"page": 2}))

    async def test_missing_private_share_password_is_unavailable(self):
        raw = {"shareId": "2345", "shortlink": "https://pan.baidu.com/s/contract", "public": 0,
               "expiredType": 1, "expiredTime": 100, "details_available": False}
        with patch.object(cli, "run", AsyncMock(return_value={"shares": [raw], "has_more": False})):
            result = await drive.list_shares(17)
        self.assertEqual(result["shares"][0]["status"], "unavailable")

    async def test_saved_history_is_not_presented_as_cloud_history(self):
        write_private(drive._records_path(17, RECORD["account_id"], "shares.json"), [{"id": "2345", "title": "旧链接"}])
        with patch.object(cli, "run", AsyncMock(return_value={"shares": [], "has_more": False})):
            result = await drive.list_shares(17)
        self.assertEqual(result["shares"], [])

    async def test_detail_requires_this_accounts_share_list_and_rechecks_owner(self):
        with patch.object(cli, "run", AsyncMock()) as call:
            with self.assertRaisesRegex(BaiduDriveError, "先查询"):
                await drive.share_detail(17, "2345")
            call.assert_not_awaited()
        index = drive._records_path(17, RECORD["account_id"], "share-pages.json")
        write_private(index, {"2345": 1})
        with patch.object(cli, "run", AsyncMock(return_value={"shares": [], "has_more": False})) as call:
            with self.assertRaisesRegex(BaiduDriveError, "不在当前账号"):
                await drive.share_detail(17, "2345")
            self.assertEqual(call.await_count, 1)

    async def test_detail_reads_native_password_without_inventing_date(self):
        write_private(drive._records_path(17, RECORD["account_id"], "share-pages.json"), {"2345": 2})
        listing = {"shares": [{"shareId": "2345", "fsIds": [ROW["fs_id"]], "public": 0,
                               "expiredType": 1, "expiredTime": 100, "typicalPath": ROW["path"]}]}
        detail = {"id": "2345", "share_url": "https://pan.baidu.com/s/contract", "passcode": "a123"}
        with patch.object(cli, "run", AsyncMock(side_effect=[listing, detail])) as call:
            result = await drive.share_detail(17, "2345")
        self.assertEqual(result["share"]["passcode"], "a123")
        self.assertNotIn("created_at", result["share"])
        self.assertEqual(call.call_args.args, (17, "share-detail", {"id": "2345"}))

    async def test_cancel_reaches_provider_and_marks_private_cache(self):
        path = drive._records_path(17, RECORD["account_id"], "shares.json")
        write_private(path, [{"id": "2345", "status": "active", "share_url": "https://pan.baidu.com/s/contract"}])
        with patch.object(cli, "run", AsyncMock(return_value={"status": "success"})) as call:
            with self.assertRaises(BaiduDriveError):
                await drive.cancel_share(17, "2345", False)
            result = await drive.cancel_share(17, "2345", True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(call.call_args.args, (17, "share-cancel", {"ids": ["2345"]}))
        self.assertEqual(read_private(path)[0]["status"], "cancelled")

    async def test_referenced_delivery_share_cannot_be_cancelled(self):
        write_private(drive._records_path(17, RECORD["account_id"], "shares.json"), [{
            "id": "2345", "status": "active", "share_url": "https://pan.baidu.com/s/contract"}])
        with patch.object(drive, "ensure_unreferenced", AsyncMock(side_effect=BaiduDriveError("资料仍在发货"))), patch.object(cli, "run", AsyncMock()) as call:
            with self.assertRaisesRegex(BaiduDriveError, "发货"):
                await drive.cancel_share(17, "2345", True)
            call.assert_not_awaited()

    async def test_invalid_share_link_never_becomes_delivery_link(self):
        with patch.object(drive, "_resolve", AsyncMock(return_value=[ROW])), patch.object(cli, "run", AsyncMock(return_value={
            "id": "2345", "share_url": "https://example.com/s/contract", "passcode": "a123"})):
            with self.assertRaises(BaiduDriveError):
                await drive.create_share(17, [], "说明", paths=[ROW["path"]])

    async def test_mutation_provider_error_does_not_report_success(self):
        with patch.object(drive, "_resolve", AsyncMock(return_value=[ROW])), patch.object(cli, "run", AsyncMock(side_effect=BaiduDriveError("真实边界失败"))):
            result = await drive.run_operation(17, "delete", {"paths": [ROW["path"]], "confirmed": True})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
