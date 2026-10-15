"""Offline checks for the per-user Quark authorization boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "app/services/quark_drive_auth.py"
spec = importlib.util.spec_from_file_location("quark_drive_auth_under_test", MODULE_PATH)
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class QuarkAuthorizationBoundaryTests(unittest.TestCase):
    def test_official_authorization_url_is_allowlisted(self) -> None:
        valid = "请打开 [授权]https://pan.quark.cn/open/v1/oauth/agent?page_code=abc123&client_id=xyz"
        self.assertEqual(
            bridge._extract_auth_link(valid),
            "https://pan.quark.cn/open/v1/oauth/agent?page_code=abc123&client_id=xyz",
        )
        self.assertIsNone(bridge._extract_auth_link("https://pan.quark.cn.evil.test/open/v1/oauth/agent?page_code=x"))
        self.assertIsNone(bridge._extract_auth_link("https://pan.quark.cn/open/v1/oauth/agent?client_id=x"))

    def test_runtime_and_credentials_are_isolated_by_site_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            _node_a, cli_a, work_a, env_a = bridge._prepare_user_runtime(17)
            _node_b, cli_b, work_b, env_b = bridge._prepare_user_runtime(18)
            self.assertNotEqual(cli_a, cli_b)
            self.assertNotEqual(work_a, work_b)
            self.assertNotEqual(env_a["HOME"], env_b["HOME"])
            self.assertEqual(stat.S_IMODE(bridge._user_dir(17).stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            self.assertFalse(bridge._has_account(17))
            self.assertFalse(bridge._has_account(18))

    def test_invalid_code_never_starts_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            with self.assertRaises(bridge.QuarkAuthError):
                asyncio.run(bridge.complete_authorization(17, "bad code\n"))
            self.assertFalse(bridge._user_dir(17).exists())

    def test_device_config_written_during_login_is_not_an_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            config_path = bridge._user_dir(17) / "skill/codex/config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({"deviceId": "", "platform": "mac"}))
            self.assertFalse(bridge._has_account(17))
            self.assertEqual(asyncio.run(bridge.get_status(17)), {"connected": False, "available": True})

            config_path.write_text(json.dumps({"accounts": [{"uid": "example"}]}))
            self.assertTrue(bridge._has_account(17))

    def test_pinned_runtime_account_survives_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            config_path = bridge._user_dir(17) / "skill/codex/config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({"deviceId": "device", "currentUserId": "account-id",
                                              "account-id": {"refreshToken": "test-token"}}))
            result = {"code": 0, "data": {"userInfo": {"nickname": "test-user"}, "vipInfo": {}}}
            with patch.object(bridge, "_run_cli", AsyncMock(return_value=result)) as cli:
                for _ in range(2):
                    status = asyncio.run(bridge.get_status(17))
                    self.assertTrue(status["connected"])
                    self.assertTrue(status["verified"])
                self.assertEqual(cli.await_count, 2)
            self.assertFalse(bridge._has_account(18))

    def test_temporary_network_failure_does_not_erase_saved_authorization(self) -> None:
        with patch.object(bridge, "_has_account", return_value=True), patch.object(
            bridge, "_run_cli", AsyncMock(side_effect=bridge.QuarkAuthError("timeout"))
        ):
            status = asyncio.run(bridge.get_status(17))
            self.assertTrue(status["connected"])
            self.assertFalse(status["verified"])

    def test_revoked_authorization_is_not_connected(self) -> None:
        with patch.object(bridge, "_has_account", return_value=True), patch.object(
            bridge, "_run_cli", AsyncMock(return_value={"code": -103})
        ):
            self.assertFalse(asyncio.run(bridge.get_status(17))["connected"])

    def test_ndjson_keeps_artifact_after_result_and_all_list_items(self) -> None:
        lines = [{"type": "list", "data": {"fid": "first"}},
                 {"type": "list", "data": {"fid": "second"}},
                 {"type": "result", "code": 0, "data": {"total": 2}},
                 {"type": "artifact", "data": {"file_path": "/private/results.jsonl", "count": 2}}]
        parsed = bridge._parse_cli_output("\n".join(json.dumps(row) for row in lines).encode())
        self.assertEqual(parsed["_items"], [{"fid": "first"}, {"fid": "second"}])
        self.assertEqual(parsed["_artifact"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
