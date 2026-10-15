"""Bounded staging and provider integrity contracts; no external cloud writes."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services import cloud_asset_storage as storage, cloud_asset_providers as providers


class AssetStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"CLOUD_ASSET_DIR": self.temp.name, "CLOUD_ASSET_MAX_BYTES": "1024"})
        self.environment.start()
        self.base = storage.directory(7, "a" * 32, 1)

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def file(self, name="说明.txt", data=b"hello"):
        return UploadFile(io.BytesIO(data), filename=name)

    async def test_staging_records_real_bytes_and_real_text(self):
        data = "这是实际测试内容".encode()
        rows = await storage.stage_uploads(7, "a" * 32, 1, [self.file(data=data)])
        self.assertEqual((self.base / "说明.txt").read_bytes(), data)
        self.assertEqual(rows[0]["size"], len(data))
        report = storage.analyze_files(self.base, rows)
        self.assertFalse(report["ai_generated"])
        self.assertEqual(report["files"][0]["text"], data.decode())
        self.assertEqual((self.base / "说明.txt").stat().st_mode & 0o777, 0o600)
        (self.base / "说明.txt").write_text("altered")
        with self.assertRaisesRegex(storage.AssetError, "校验失败"):
            storage.analyze_files(self.base, rows)

    async def test_batch_quota_and_casefold_collision(self):
        with self.assertRaisesRegex(storage.AssetError, "同名"):
            await storage.stage_uploads(7, "a" * 32, 1, [self.file("A.txt"), self.file("a.txt")])
        with self.assertRaisesRegex(storage.AssetError, "大小"):
            await storage.stage_uploads(7, "a" * 32, 1, [self.file("1.txt", b"a" * 600), self.file("2.txt", b"b" * 600)])

    def test_unsafe_names_and_nonpublic_urls(self):
        for name in ("../file", "a\\file", "..", "a\n.txt", "/file"):
            with self.subTest(name=name), self.assertRaises(storage.AssetError):
                storage.filename(name)
        for url in ("http://127.0.0.1/x", "http://10.0.0.1/x", "http://[::1]/x", "http://localhost/x",
                    "https://host.local/x", "https://user:password@example.com/x", "https://example.com:8080/x",
                    "https://example.com:invalid/x", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(storage.AssetError):
                storage.public_url(url)
        self.assertEqual(storage.public_url("https://example.com/a.zip"), "https://example.com/a.zip")

    def test_private_staging_rejects_symlink_files_and_public_roots(self):
        (self.base / "real.txt").write_text("real")
        (self.base / "link.txt").symlink_to(self.base / "real.txt")
        with self.assertRaises(storage.AssetError):
            storage.describe(self.base / "link.txt")
        with patch.dict(os.environ, {"CLOUD_ASSET_DIR": str(storage.PROJECT_ROOT / "frontend/public/assets")}):
            with self.assertRaises(storage.AssetError):
                storage.root()

    async def test_provider_opaque_hash_requires_actual_download(self):
        local = self.base / "test.txt"
        local.write_bytes(b"hello")
        row = storage.describe(local)
        remote = {"fid": "123", "size": 5, "content_hash": "provider-defined-not-md5"}
        self.assertTrue(providers._same_file(remote, row))

        async def download(owner, fid, directory):
            output = directory / "test.txt"
            output.write_bytes(b"hello")
            return output

        with patch.object(providers, "_quark_download", side_effect=download) as call:
            self.assertEqual(await providers._verify_remote(7, "quark", remote, row, self.base), "download-sha256")
            call.assert_awaited_once()
        async def different(owner, fid, directory):
            output = directory / "test.txt"
            output.write_bytes(b"world")
            return output
        with patch.object(providers, "_quark_download", side_effect=different):
            with self.assertRaisesRegex(storage.AssetError, "内容校验"):
                await providers._verify_remote(7, "quark", remote, row, self.base)
        with patch.object(providers, "_quark_download", AsyncMock()) as call:
            self.assertEqual(await providers._verify_remote(7, "quark", {**remote, "md5": row["md5"]}, row, self.base), "md5")
            call.assert_not_awaited()

    async def test_unknown_share_without_saved_record_cannot_create_again(self):
        target = {"provider": "quark", "account_id": "test", "files": [{"fid": "123"}],
                  "status": "unknown", "last_operation": "shares", "share_outcome_unknown": True}
        with patch.object(providers, "account_id", return_value="test"), \
             patch.object(providers.qd, "list_shares", AsyncMock(return_value={"shares": []})), \
             patch.object(providers.qd, "create_share", AsyncMock()) as create:
            with self.assertRaisesRegex(storage.AssetError, "停止重复创建"):
                await providers.share(7, "test", target, 1)
            create.assert_not_awaited()

    async def test_source_transfer_requires_complete_listing_and_confirmed_count(self):
        source = {"provider": "baidu", "account_id": "test", "url": "https://pan.baidu.com/s/test"}
        row = {"filename": "资料.txt", "isdir": False, "size": 5, "path": "/apps/bdpan/资料.txt"}
        directory = AsyncMock(side_effect=[[{"filename": "闲鱼来源转存", "isdir": True, "path": "/apps/bdpan/闲鱼来源转存"}],
                                           [{"filename": "a" * 32, "isdir": True, "path": "/apps/bdpan/闲鱼来源转存/" + "a" * 32}]])
        for outcome, listing, error in (
            ({"status": "submitted"}, {"files": [row]}, "尚未全部确认"),
            ({"count": 101}, {"files": [row] * 100, "has_more": True}, "普通文件"),
            ({"count": 2}, {"files": [row], "has_more": False}, "数量"),
        ):
            directory.reset_mock(side_effect=True)
            directory.side_effect = [[{"filename": "闲鱼来源转存", "isdir": True, "path": "/apps/bdpan/闲鱼来源转存"}],
                                    [{"filename": "a" * 32, "isdir": True, "path": "/apps/bdpan/闲鱼来源转存/" + "a" * 32}]]
            with self.subTest(outcome=outcome), patch.object(providers, "account_id", return_value="test"), \
                 patch.object(providers.bd, "OPERATIONS", (*providers.bd.OPERATIONS, "share-transfer")), \
                 patch.object(providers.bd, "_directory", directory), \
                 patch.object(providers.bd, "run_operation", AsyncMock(return_value=outcome)), \
                 patch.object(providers.bd, "list_files", AsyncMock(return_value=listing)):
                with self.assertRaisesRegex(storage.AssetError, error):
                    await providers.transfer_source(7, "a" * 32, source)

    async def test_unavailable_transfer_is_rejected_before_creating_cloud_directories(self):
        source = {"provider": "baidu", "account_id": "test", "url": "https://pan.baidu.com/s/test"}
        with patch.object(providers, "account_id", return_value="test"), \
             patch.object(providers.bd, "_directory", AsyncMock()) as directory, \
             patch.object(providers.bc, "run", AsyncMock()) as call:
            with self.assertRaisesRegex(storage.AssetError, "暂不支持分享转存"):
                await providers.transfer_source(7, "a" * 32, source)
            directory.assert_not_awaited()
            call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
