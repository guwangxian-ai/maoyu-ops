"""Provider result and persistence boundaries; no requests to Quark."""

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import quark_drive as drive


class QuarkFileAndShareTests(unittest.TestCase):
    def test_reads_complete_artifact_instead_of_five_item_preview(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            path = Path(directory) / "user-17/results.jsonl"
            path.parent.mkdir()
            rows = [{"fid": f"file-{index}", "filename": f"file {index}", "private_field": "hidden"} for index in range(7)]
            path.write_text("\n".join(json.dumps(row) for row in rows))
            result = {"code": 0, "data": {"file_list": rows[:5]},
                      "_artifact": {"file_path": str(path), "count": 7}}
            with patch.object(drive, "_has_account", return_value=True), patch.object(drive, "_run_cli", AsyncMock(return_value=result)):
                files = asyncio.run(drive.list_files(17))
            self.assertEqual(files["total"], 7)
            self.assertNotIn("private_field", files["files"][0])
            self.assertFalse(path.exists())

    def test_rejects_artifact_outside_current_users_private_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            path = Path(directory) / "user-18/results.jsonl"
            path.parent.mkdir()
            path.write_text('{"fid":"private-file"}')
            with self.assertRaises(drive.QuarkAuthError):
                drive._read_file_artifact(17, {"file_path": str(path), "count": 1})
            self.assertTrue(path.exists())

    def test_rejects_option_injection_before_starting_cli(self):
        with patch.object(drive, "_run_cli", AsyncMock()) as cli:
            with self.assertRaises(drive.QuarkAuthError):
                asyncio.run(drive.create_share(17, ["--help"], "title", 2, 1))
            cli.assert_not_called()

    def test_type_only_filter_uses_browsed_provider_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            path = Path(directory) / "user-17/results.jsonl"
            path.parent.mkdir()
            path.write_text('\n'.join(json.dumps(row) for row in [
                {"fid": "doc", "filename": "文档.docx", "category": 4},
                {"fid": "video", "filename": "视频.mp4", "category": 1}]))
            result = {"code": 0, "data": {}, "_artifact": {"file_path": str(path), "count": 2}}
            with patch.object(drive, "_has_account", return_value=True), patch.object(drive, "_run_cli", AsyncMock(return_value=result)) as cli:
                listing = asyncio.run(drive.list_files(17, "0", "", "doc"))
            self.assertEqual([row["fid"] for row in listing["files"]], ["doc"])
            self.assertEqual(cli.call_args.args[1], "browse")

    def test_shares_survive_reload_and_are_private_to_site_user(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"QUARK_AUTH_DIR": directory}):
            (Path(directory) / "user-17").mkdir()
            with patch.object(drive, "_has_account", return_value=True), patch.object(drive, "_run_cli", AsyncMock(return_value={
                "code": 0, "data": {"share_url": "https://pan.quark.cn/s/test-link", "passcode": "test"}
            })):
                created = asyncio.run(drive.create_share(17, ["file-1"], "title", 2, 1))
            self.assertTrue(created["saved"])
            self.assertEqual(asyncio.run(drive.list_shares(17))["shares"], [created["share"]])
            self.assertEqual(asyncio.run(drive.list_shares(18))["shares"], [])
            self.assertEqual(stat.S_IMODE(drive._share_path(17).stat().st_mode), 0o600)

    def test_remote_share_url_is_returned_when_record_cannot_be_saved(self):
        with patch.object(drive, "_has_account", return_value=True), patch.object(drive, "_read_shares", return_value=[]), patch.object(
            drive, "_run_cli", AsyncMock(return_value={"code": 0, "data": {"share_url": "https://pan.quark.cn/s/test-link"}})
        ), patch.object(drive, "_write_shares", side_effect=OSError("disk full")):
            result = asyncio.run(drive.create_share(17, ["file-1"], "title", 1, 1))
        self.assertFalse(result["saved"])
        self.assertEqual(result["share"]["share_url"], "https://pan.quark.cn/s/test-link")


if __name__ == "__main__":
    unittest.main()
