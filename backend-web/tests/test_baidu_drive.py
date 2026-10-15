"""Provider contract and account boundaries; no real cloud file mutations."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import UploadFile
from app.services import baidu_drive as drive
from app.services import baidu_drive_auth as auth
from app.services import baidu_drive_mcp as gateway


PROFILE = {"account_id": "a" * 32, "nickname": "测试账号", "vip_type": 0,
           "used_bytes": 12, "capacity_bytes": 1024}
ROW = {"fs_id": 9876543210123456789, "path": "/资料/说明.txt", "server_filename": "说明.txt",
       "size": 12, "isdir": 0, "category": 4, "server_mtime": 1700000000}


class BaiduBoundaries(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BAIDU_AUTH_DIR": self.directory.name})
        self.environment.start()
        auth._locks.clear()
        auth.write_private(auth.user_dir(17) / "application.json", {"mode": "experience"})

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()
        auth._locks.clear()

    def account(self, user_id=17, **extras):
        record = {**PROFILE, "access_token": "test-access-token", "auth_mode": "experience", **extras}
        auth.write_private(auth.user_dir(user_id) / "account.json", record)
        return record

    async def test_authorization_survives_reload_without_exposing_tokens(self):
        with patch.object(auth, "_profile", AsyncMock(return_value=(PROFILE, ""))):
            status = await auth.complete_authorization(17, "test-access-token", "access_token")
            again = await auth.get_status(17)
        self.assertTrue(status["connected"] and again["verified"])
        self.assertNotIn("test-access-token", json.dumps(again))
        self.assertFalse((await auth.get_status(18))["connected"])
        self.assertEqual(stat.S_IMODE((auth.user_dir(17) / "account.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(auth.user_dir(17).stat().st_mode), 0o700)

    async def test_failed_switch_keeps_previous_account(self):
        previous = self.account()
        with patch.object(auth, "_profile", AsyncMock(side_effect=auth.BaiduTokenExpired("expired"))):
            with self.assertRaises(auth.BaiduTokenExpired):
                await auth.complete_authorization(17, "different-token", "access_token")
        self.assertEqual(auth._account(17), previous)

    async def test_temporary_outage_keeps_authorization(self):
        self.account()
        with patch.object(auth, "_profile", AsyncMock(side_effect=auth.BaiduDriveError("temporary network failure"))):
            result = await auth.get_status(17)
        self.assertTrue(result["connected"])
        self.assertFalse(result["verified"])
        self.assertIsNotNone(auth._account(17))

    async def test_expired_experience_token_requires_real_reauthorization(self):
        self.account(expires_at=1)
        status = await auth.get_status(17)
        self.assertFalse(status["connected"])
        self.assertIn("过期", status["message"])
        self.assertIsNotNone(auth._account(17))

    async def test_formal_refresh_uses_bound_app_and_preserves_rotated_token(self):
        self.account(expires_at=1, refresh_token="test-refresh", refresh_client_id="original-app",
                     refresh_client_secret="test-secret")
        with patch.object(auth, "provider_json", AsyncMock(return_value={"access_token": "rotated-token",
             "refresh_token": "rotated-refresh", "expires_in": 300})) as provider:
            record, token = await auth.account_token(17)
        self.assertEqual(token, "rotated-token")
        self.assertEqual(record["refresh_token"], "rotated-refresh")
        self.assertEqual(provider.call_args.kwargs["data"]["client_id"], "original-app")
        self.assertEqual(auth._account(17)["access_token"], "rotated-token")

    async def test_invalid_refresh_expiration_keeps_saved_account(self):
        previous = self.account(expires_at=1, refresh_token="test-refresh", refresh_client_id="original-app",
                                refresh_client_secret="test-secret")
        with patch.object(auth, "provider_json", AsyncMock(return_value={"access_token": "rotated-token", "expires_in": "invalid"})):
            with self.assertRaises(auth.BaiduDriveError):
                await auth.account_token(17)
        self.assertEqual(auth._account(17), previous)

    async def test_oauth_state_and_official_origin_are_checked(self):
        started = await auth.start_authorization(17)
        pending = auth.read_private(auth.user_dir(17) / "pending.json")
        self.assertTrue(started["authorization_url"].startswith("https://openapi.baidu.com/oauth/2.0/authorize?"))
        for value in ("https://openapi.baidu.com.evil.test/#access_token=test-token",
                      "https://openapi.baidu.com:invalid/#access_token=test-token",
                      "https://openapi.baidu.com/oauth/2.0/login_success#access_token=test-token&state=wrong"):
            with self.assertRaises(auth.BaiduDriveError):
                auth.parse_credential(17, value, "access_token")
        value = "https://openapi.baidu.com/oauth/2.0/login_success#access_token=test-token&expires_in=600&state=" + pending["state"]
        self.assertEqual(auth.parse_credential(17, value, "access_token")["value"], "test-token")

    async def test_app_secret_not_readable_in_config_and_blank_keeps_it(self):
        await auth.save_config(17, "application", "test-app-key", "test-secret")
        config = await auth.save_config(17, "application", "test-app-key", "")
        self.assertTrue(config["secret_configured"])
        self.assertNotIn("test-secret", json.dumps(config))
        self.assertEqual(auth._config(17)["client_secret"], "test-secret")
        with self.assertRaises(auth.BaiduDriveError):
            await auth.save_config(17, "application", "another-app-key", "")

    async def test_busy_upload_blocks_logout_and_switch(self):
        self.account()
        async with auth.exclusive(17):
            with self.assertRaises(auth.BaiduDriveError):
                await auth.logout(17)
            with self.assertRaises(auth.BaiduDriveError):
                await auth.complete_authorization(17, "different-token", "access_token")

    async def test_logout_does_not_remove_other_users_account(self):
        self.account(17)
        self.account(18)
        await auth.logout(17)
        self.assertIsNone(auth._account(17))
        self.assertIsNotNone(auth._account(18))

    async def test_share_records_are_scoped_to_site_user_and_cloud_account(self):
        self.account()
        with patch.object(drive, "call_tool", AsyncMock(return_value={"link": "https://pan.baidu.com/s/test-share", "pwd": "a123"})):
            shared = await drive.create_share(17, [str(ROW["fs_id"])], "说明", 1, "a123")
        self.assertEqual((await drive.list_shares(17))["shares"], [shared["share"]])
        self.account(account_id="b" * 32)
        self.assertEqual((await drive.list_shares(17))["shares"], [])
        self.account(18)
        self.assertEqual((await drive.list_shares(18))["shares"], [])
        self.account(account_id="a" * 32)
        self.assertEqual((await drive.list_shares(17))["total"], 1)

    async def test_share_created_remotely_is_preserved_if_history_save_fails(self):
        self.account()
        with patch.object(drive, "call_tool", AsyncMock(return_value={"link": "https://pan.baidu.com/s/test-share", "pwd": "a123"})), patch.object(drive, "write_private", side_effect=auth.BaiduDriveError("disk full")):
            shared = await drive.create_share(17, ["123"], "说明", 1)
        self.assertFalse(shared["saved"])
        self.assertEqual(shared["share"]["share_url"], "https://pan.baidu.com/s/test-share")

    async def test_mutations_require_confirmation_and_reject_root(self):
        with patch.object(drive, "call_tool", AsyncMock()) as tool:
            with self.assertRaises(auth.BaiduDriveError):
                await drive.run_operation(17, "delete", {"paths": ["/资料"]})
            with self.assertRaises(auth.BaiduDriveError):
                await drive.run_operation(17, "delete", {"paths": ["/"], "confirmed": True})
            with self.assertRaises(auth.BaiduDriveError):
                await drive.run_operation(17, "move", {"paths": ["/资料"], "target_dir": "/资料/内部", "confirmed": True})
        tool.assert_not_called()

    async def test_partial_manager_failure_is_not_reported_as_complete(self):
        self.account()
        with patch.object(drive, "call_tool", AsyncMock(return_value={"errno": 0, "info": [{"path": "/资料/说明.txt", "errno": -9}]})):
            result = await drive.run_operation(17, "delete", {"paths": ["/资料/说明.txt"], "confirmed": True})
        self.assertEqual(result["data"]["status"], "partial")
        self.assertIn("未完成", result["message"])

    async def test_make_dir_encodes_chinese_path_as_documented(self):
        self.account()
        with patch.object(drive, "call_tool", AsyncMock(return_value={"fsid": 123, "path": "/资料/新目录"})) as tool:
            await drive.run_operation(17, "create-folder", {"dir": "/资料", "name": "新目录", "confirmed": True})
        self.assertEqual(tool.call_args.args[1], "make_dir")
        self.assertEqual(tool.call_args.args[2], {"path": "%2F%E8%B5%84%E6%96%99%2F%E6%96%B0%E7%9B%AE%E5%BD%95", "rtype": 0})

    async def test_listing_preserves_large_ids_and_uses_real_pagination(self):
        self.account()
        with patch.object(drive, "provider_json", AsyncMock(return_value={"errno": 0, "list": [ROW], "has_more": 0})) as provider:
            result = await drive.list_files(17, "/资料", page=3, page_size=50)
        self.assertEqual(result["files"][0]["fs_id"], "9876543210123456789")
        self.assertEqual(provider.call_args.kwargs["params"]["start"], 100)
        self.assertFalse(result["has_more"])

    async def test_category_filter_does_not_drop_items_after_first_mixed_page(self):
        self.account()
        non_doc = {**ROW, "fs_id": 123, "category": 1}
        with patch.object(drive, "provider_json", AsyncMock(side_effect=[{"list": [non_doc], "has_more": 1}, {"list": [ROW], "has_more": 0}])):
            result = await drive.list_files(17, "/资料", category="doc")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["files"][0]["category"], 4)

    async def test_upload_verifies_block_md5_before_final_create(self):
        content = b"real-test-body"
        upload = UploadFile(io.BytesIO(content), filename="说明.txt", size=len(content))
        try:
            with patch.object(drive, "provider_json", AsyncMock(side_effect=[{"uploadid": "test-upload", "block_list": [0]}, {"md5": "wrong"}])) as provider:
                with self.assertRaises(auth.BaiduDriveError):
                    await drive._upload_file("test-token", upload, "/资料/说明.txt", len(content))
                self.assertEqual(provider.await_count, 2)
            with patch.object(drive, "provider_json", AsyncMock(side_effect=[{"uploadid": "test-upload", "block_list": [0]}, {"md5": hashlib.md5(content).hexdigest()}, ROW])) as provider:
                result = await drive._upload_file("test-token", upload, "/资料/说明.txt", len(content))
            self.assertEqual(result["fs_id"], str(ROW["fs_id"]))
            self.assertEqual(provider.call_args_list[0].kwargs["data"]["rtype"], "0")
        finally:
            await upload.close()

    async def test_duplicate_upload_names_fail_before_remote_writes(self):
        files = [UploadFile(io.BytesIO(b"test"), filename="same.txt", size=4) for _ in range(2)]
        try:
            with patch.object(drive, "_upload_file", AsyncMock()) as provider:
                with self.assertRaises(auth.BaiduDriveError):
                    await drive.upload_files(17, files, "/资料")
            provider.assert_not_called()
        finally:
            for file in files:
                await file.close()


class McpContracts(unittest.TestCase):
    def test_nested_failures_are_rejected_like_top_level_failures(self):
        for data in ({"success": False}, {"status": "failed"}, {"code": 403}):
            with self.subTest(data=data):
                result = SimpleNamespace(structuredContent={"success": True, "data": data}, isError=False)
                with self.assertRaises(auth.BaiduDriveError):
                    gateway._json_result(result)

    def test_remote_json_data_and_auth_failures_are_not_fake_successes(self):
        result = SimpleNamespace(structuredContent=None, isError=False,
            content=[SimpleNamespace(type="text", text=json.dumps({"errno": 0, "data": {"list": [ROW]}}))])
        self.assertEqual(gateway._json_result(result)["list"], [ROW])
        result.content[0].text = '{"errno":111}'
        with self.assertRaises(auth.BaiduTokenExpired):
            gateway._json_result(result)
        result.content[0].text = 'opaque message without JSON'
        with self.assertRaises(auth.BaiduDriveError):
            gateway._json_result(result)

    def test_tool_schema_converts_documented_json_string_arrays(self):
        schema = {"properties": {"fsid_list": {"type": "string"}, "period": {"type": "integer"}}, "required": ["fsid_list"]}
        data = gateway.tool_arguments(schema, {"fsid_list": ["123", "456"], "period": 7, "unrecognized": "skip"})
        self.assertEqual(json.loads(data["fsid_list"]), ["123", "456"])
        self.assertNotIn("unrecognized", data)

    def test_credentials_are_redacted_from_provider_log_urls(self):
        value = 'HTTP Request: GET https://mcp-pan.baidu.com/sse?access_token=private-token&client_secret=private-secret'
        message = auth.redact(value)
        self.assertNotIn("private-token", message)
        self.assertNotIn("private-secret", message)

    def test_nullable_mcp_schema_encodes_json_arrays_and_string_numbers(self):
        schema = {"properties": {"fsid_list": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                  "period": {"type": "string"}, "rtype": {"type": "integer", "default": 0}}, "required": ["fsid_list", "rtype"]}
        data = gateway.tool_arguments(schema, {"fsid_list": ["123"], "period": 7})
        self.assertEqual(data, {"fsid_list": '["123"]', "period": "7", "rtype": 0})


if __name__ == "__main__":
    unittest.main()
