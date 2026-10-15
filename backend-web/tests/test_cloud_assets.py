"""Persistent asset workflow regressions using real ORM rows and private files.

Only the external cloud boundary is stubbed. No credentials, cloud files, shares,
orders, or messages are accessed. SQLite does not emulate MySQL row locking.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import BigInteger, create_engine, func, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))

from app.services import cloud_assets as assets
from app.services import cloud_asset_providers as providers
from app.services import cloud_asset_storage as storage
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.cloud_asset import CloudAsset
from common.models.cloud_drive_job import CloudDriveJob
from common.models.xy_catalog_item import XYCatalogItem


@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


@compiles(LONGTEXT, "sqlite")
def _sqlite_longtext(type_, compiler, **kw):
    return "TEXT"


class AsyncSession:
    """Async-shaped access to actual isolated SQLAlchemy transactions."""

    def __init__(self, engine):
        self.db = Session(engine, expire_on_commit=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.db.close()

    def add(self, row):
        self.db.add(row)

    async def scalar(self, statement):
        return self.db.scalar(statement)

    async def scalars(self, statement):
        return self.db.scalars(statement)

    async def execute(self, statement):
        return self.db.execute(statement)

    async def get(self, model, key):
        return self.db.get(model, key)

    async def flush(self):
        self.db.flush()

    async def commit(self):
        self.db.commit()


class CloudAssetWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cloud-asset-tests-")
        self.engine = create_engine("sqlite://")
        for model in (CloudAsset, CloudDriveJob, Card, CardItemRelation, XYCatalogItem):
            model.__table__.create(self.engine)
        self.upload_failure = {}
        self.share_failure = {}
        self.patches = [
            patch.dict(os.environ, {"CLOUD_ASSET_DIR": self.temp.name, "CLOUD_ASSET_MAX_BYTES": "1048576"}),
            patch.object(assets, "async_session_maker", lambda: AsyncSession(self.engine)),
            patch.object(assets, "_running", {}),
            patch.object(assets, "_gate", asyncio.Semaphore(2)),
            patch.object(providers, "account_id", side_effect=lambda owner, provider: f"{owner}-{provider}"),
            patch.object(providers, "upload", AsyncMock(side_effect=self.cloud_upload)),
            patch.object(providers, "share", AsyncMock(side_effect=self.cloud_share)),
        ]
        for patcher in self.patches:
            patcher.start()

    async def asyncTearDown(self):
        await assets.shutdown()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.engine.dispose()
        self.temp.cleanup()

    @staticmethod
    def file(data=b"real local course contents", filename="lesson.txt"):
        return UploadFile(filename=filename, file=io.BytesIO(data))

    async def create(self, selected=None, **kwargs):
        return await assets.upload(7, "课程资料", selected or ["quark", "baidu"], [self.file(**kwargs)])

    async def cloud_upload(self, owner, identifier, version, base, manifest, target, checkpoint):
        failure = self.upload_failure.get(target["provider"])
        if failure:
            raise failure
        files = []
        for index, row in enumerate(manifest):
            # Verify that the actual immutable staging passed to the provider exists.
            self.assertEqual(hashlib.sha256((base / row["name"]).read_bytes()).hexdigest(), row["sha256"])
            files.append({**row, "fid": f"{index + version:032x}", "fs_id": index + version,
                          "path": f"/apps/bdpan/{identifier}/v{version}/{row['name']}", "test_version": version})
        await checkpoint({"files": files})
        return {"files": files}

    async def cloud_share(self, owner, label, target, period):
        failure = self.share_failure.get(target["provider"])
        if failure:
            raise failure
        version = target["files"][0]["test_version"]
        host = "pan.quark.cn" if target["provider"] == "quark" else "pan.baidu.com"
        return {"share_url": f"https://{host}/s/test-v{version}", "passcode": "a123", "expires_at": None}

    async def drain(self):
        tasks = list(assets._running.values())
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        await asyncio.sleep(0)

    async def run_job(self, identifier, operation, params=None):
        await assets.enqueue(7, identifier, operation, params)
        await self.drain()
        return await assets.get(7, identifier)

    async def publish(self, identifier):
        await self.run_job(identifier, "sync")
        return await self.run_job(identifier, "shares", {"period": 1})

    def read(self, model, key):
        with Session(self.engine) as session:
            row = session.get(model, key)
            session.expunge(row)
            return row

    def count(self, model):
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(model))

    def mutate(self, identifier, **changes):
        with Session(self.engine) as session:
            row = session.get(CloudAsset, identifier)
            for key, value in changes.items():
                setattr(row, key, value)
            session.commit()

    def seed_job(self, identifier, job_id, *, operation="sync", version=1, status="failed", **params):
        with Session(self.engine) as session:
            session.add(CloudDriveJob(id=job_id, owner_id=7, provider="multi", operation=operation,
                                     status=status, params={"asset_id": identifier, "version": version, **params}))
            session.commit()

    def seed_item(self, item_id, owner=7):
        with Session(self.engine) as session:
            session.add(XYCatalogItem(owner_id=owner, account_pk=1, item_id=item_id,
                                      created_at=datetime.now(timezone.utc)))
            session.commit()

    async def test_complete_flow_publishes_both_platforms_and_idempotent_card(self):
        created = await self.create()
        identifier = created["id"]
        self.assertEqual(created["status"], "staged")
        synced = await self.run_job(identifier, "sync")
        self.assertEqual(synced["status"], "ready")
        self.assertFalse(synced["deliverable"])
        published = await self.run_job(identifier, "shares", {"period": 1})
        self.assertEqual(published["status"], "deliverable")
        self.assertIn("https://pan.quark.cn/s/test-v1", published["delivery_content"])
        self.assertIn("https://pan.baidu.com/s/test-v1", published["delivery_content"])
        self.assertTrue(all("account_id" not in row and "last_job_id" not in row for row in published["targets"]))
        self.seed_item("item-1")
        first = await assets.create_card(7, identifier, ["item-1", "item-1"])
        second = await assets.create_card(7, identifier, ["item-1"])
        self.assertEqual(first["card_id"], second["card_id"])
        self.assertEqual(self.count(Card), 1)
        self.assertEqual(self.count(CardItemRelation), 1)
        self.assertEqual(self.read(Card, int(first["card_id"])).text_content, published["delivery_content"])
        self.assertEqual(providers.upload.await_count, 2)
        self.assertEqual(providers.share.await_count, 2)
        with Session(self.engine) as session:
            self.assertEqual(set(session.scalars(select(CloudDriveJob.status))), {"succeeded"})

    async def test_partial_sync_does_not_create_shares_or_delivery_card(self):
        identifier = (await self.create())["id"]
        self.upload_failure["baidu"] = storage.AssetError("cloud quota unavailable")
        result = await self.run_job(identifier, "sync")
        self.assertEqual(result["status"], "partial")
        self.assertEqual([row["status"] for row in result["targets"]], ["uploaded", "failed"])
        self.assertIsNone(self.read(CloudAsset, identifier).published)
        with self.assertRaises(storage.AssetError):
            await assets.enqueue(7, identifier, "shares", {"period": 1})
        with self.assertRaises(storage.AssetError):
            await assets.create_card(7, identifier, [])
        self.assertEqual(self.count(CloudDriveJob), 1)
        self.assertEqual(self.count(Card), 0)
        providers.share.assert_not_awaited()

    async def test_new_version_partial_share_preserves_snapshot_and_card_then_updates_atomically(self):
        identifier = (await self.create())["id"]
        await self.publish(identifier)
        card_id = int((await assets.create_card(7, identifier, []))["card_id"])
        before = self.read(CloudAsset, identifier).published
        before_content = self.read(Card, card_id).text_content
        await assets.replace_version(7, identifier, [self.file(b"second version")])
        self.share_failure["baidu"] = storage.AssetError("sharing permission unavailable")
        partial = await self.publish(identifier)
        self.assertEqual(partial["version"], 2)
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["deliverable"])
        self.assertEqual(self.read(CloudAsset, identifier).published, before)
        self.assertEqual(self.read(Card, card_id).text_content, before_content)
        self.assertEqual([row["status"] for row in partial["targets"]], ["shared", "failed"])
        self.share_failure.clear()
        await assets.retry(7, identifier)
        await self.drain()
        updated = self.read(CloudAsset, identifier)
        self.assertEqual(updated.published["version"], 2)
        self.assertEqual(updated.card_id, card_id)
        self.assertEqual(self.count(Card), 1)
        content = self.read(Card, card_id).text_content
        self.assertIn("版本 2", content)
        self.assertIn("https://pan.baidu.com/s/test-v2", content)
        self.assertNotIn("test-v1", content)

    async def test_expired_share_cannot_publish_or_overwrite_old_card(self):
        identifier = (await self.create())["id"]
        await self.publish(identifier)
        card_id = int((await assets.create_card(7, identifier, []))["card_id"])
        original = self.read(Card, card_id).text_content
        await assets.replace_version(7, identifier, [self.file(b"replacement")])
        await self.run_job(identifier, "sync")

        async def expired(*args):
            result = await self.cloud_share(*args)
            result["expires_at"] = "2000-01-01T00:00:00Z"
            return result

        providers.share.side_effect = expired
        result = await self.run_job(identifier, "shares", {"period": 1})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["published_version"], 1)
        self.assertEqual(self.read(Card, card_id).text_content, original)

    async def test_account_switch_and_foreign_owner_are_rejected_before_cloud_side_effect(self):
        identifier = (await self.create())["id"]
        with self.assertRaises(storage.AssetError):
            await assets.get(8, identifier)
        with self.assertRaises(storage.AssetError):
            await assets.replace_version(8, identifier, [self.file(b"foreign")])
        providers.account_id.side_effect = lambda owner, provider: "different-account"
        with self.assertRaisesRegex(storage.AssetError, "账号已切换"):
            await assets.enqueue(7, identifier, "sync")
        providers.upload.assert_not_awaited()
        self.assertEqual(self.count(CloudDriveJob), 0)
        self.assertEqual(self.read(CloudAsset, identifier).version, 1)

    async def test_foreign_catalog_item_does_not_create_card_or_relation(self):
        identifier = (await self.create())["id"]
        await self.publish(identifier)
        self.seed_item("foreign-item", owner=8)
        with self.assertRaisesRegex(storage.AssetError, "不属于"):
            await assets.create_card(7, identifier, ["foreign-item"])
        self.assertIsNone(self.read(CloudAsset, identifier).card_id)
        self.assertEqual(self.count(Card), 0)
        self.assertEqual(self.count(CardItemRelation), 0)

    async def test_second_job_is_rejected_while_first_is_running(self):
        identifier = (await self.create())["id"]
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(*args):
            started.set()
            await release.wait()
            return await self.cloud_upload(*args)

        providers.upload.side_effect = blocked
        await assets.enqueue(7, identifier, "sync")
        await asyncio.wait_for(started.wait(), 2)
        with self.assertRaisesRegex(storage.AssetError, "已有任务"):
            await assets.enqueue(7, identifier, "sync")
        with self.assertRaises(storage.AssetError):
            await assets.replace_version(7, identifier, [self.file(b"unexpected replacement")])
        self.assertEqual(self.count(CloudDriveJob), 1)
        release.set()
        await self.drain()
        self.assertEqual(providers.upload.await_count, 2)
        self.assertEqual(self.read(CloudAsset, identifier).status, "ready")

    async def test_stale_checkpoint_finish_and_worker_cannot_mutate_new_claim(self):
        identifier = (await self.create())["id"]
        old, current = "a" * 32, "b" * 32
        self.seed_job(identifier, old)
        self.seed_job(identifier, current, version=2, status="queued")
        targets = self.read(CloudAsset, identifier).targets
        for row in targets:
            row.update(last_job_id=current, status="pending")
        self.mutate(identifier, version=2, status="syncing", targets=targets)
        for version, job_id in ((1, current), (2, old)):
            with self.subTest(version=version, job_id=job_id):
                with self.assertRaisesRegex(storage.AssetError, "任务已失效"):
                    await assets._change(7, identifier, "quark", {"status": "shared"}, version=version, job_id=job_id)
                with self.assertRaisesRegex(storage.AssetError, "任务已失效"):
                    await assets._finish(7, identifier, job_id, "sync", version)
        await assets._run(7, identifier, old)
        row = self.read(CloudAsset, identifier)
        self.assertEqual(row.version, 2)
        self.assertEqual(row.status, "syncing")
        self.assertEqual(row.targets, targets)
        self.assertEqual(self.read(CloudDriveJob, old).status, "interrupted")
        self.assertEqual(self.read(CloudDriveJob, current).status, "queued")
        providers.upload.assert_not_awaited()

    async def test_retry_uses_exact_claim_not_random_id_or_timestamp_and_rejects_old_version(self):
        identifier = (await self.create())["id"]
        await self.run_job(identifier, "sync")
        exact, unrelated = "1" * 32, "f" * 32
        self.seed_job(identifier, exact, operation="shares", period=7)
        self.seed_job(identifier, unrelated, operation="sync")
        targets = self.read(CloudAsset, identifier).targets
        for row in targets:
            row.update(last_job_id=exact)
        self.mutate(identifier, targets=targets, status="partial")
        await assets.retry(7, identifier)
        await self.drain()
        self.assertEqual(self.read(CloudAsset, identifier).status, "deliverable")
        self.assertEqual([call.args[3] for call in providers.share.await_args_list], [7, 7])
        self.assertEqual(providers.upload.await_count, 2)
        await assets.replace_version(7, identifier, [self.file(b"v2")])
        self.mutate(identifier, status="failed", targets=targets)
        count = self.count(CloudDriveJob)
        with self.assertRaisesRegex(storage.AssetError, "没有可恢复"):
            await assets.retry(7, identifier)
        self.assertEqual(self.count(CloudDriveJob), count)

    async def test_unknown_share_cannot_be_bypassed_by_sync(self):
        identifier = (await self.create())["id"]
        await self.run_job(identifier, "sync")
        self.share_failure["baidu"] = OSError("lost acknowledgement")
        result = await self.run_job(identifier, "shares", {"period": 1})
        self.assertEqual(result["status"], "partial")
        unknown = result["targets"][1]
        self.assertEqual(unknown["status"], "unknown")
        self.assertTrue(unknown["share_outcome_unknown"])
        count = self.count(CloudDriveJob)
        with self.assertRaisesRegex(storage.AssetError, "未确认的分享"):
            await assets.enqueue(7, identifier, "sync")
        # The durable uncertainty marker still protects a subsequently changed status.
        targets = self.read(CloudAsset, identifier).targets
        targets[1]["status"] = "failed"
        self.mutate(identifier, targets=targets)
        with self.assertRaises(storage.AssetError):
            await assets.enqueue(7, identifier, "sync")
        self.assertEqual(self.count(CloudDriveJob), count)
        self.assertEqual(providers.upload.await_count, 2)

    async def test_recover_marks_inflight_results_unknown_and_preserves_published(self):
        identifier = (await self.create())["id"]
        await self.publish(identifier)
        original = deepcopy(self.read(CloudAsset, identifier).published)
        job_id = "c" * 32
        self.seed_job(identifier, job_id, operation="shares", status="running", period=1)
        targets = self.read(CloudAsset, identifier).targets
        targets[0].update(status="sharing", last_operation="shares", last_job_id=job_id)
        targets[1].update(status="uploaded", last_job_id=job_id)
        self.mutate(identifier, status="sharing", targets=targets)
        before_calls = providers.share.await_count
        await assets.recover()
        recovered = self.read(CloudAsset, identifier)
        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.targets[0]["status"], "unknown")
        self.assertTrue(recovered.targets[0]["share_outcome_unknown"])
        self.assertEqual(recovered.targets[1]["status"], "uploaded")
        self.assertEqual(recovered.published, original)
        self.assertEqual(self.read(CloudDriveJob, job_id).status, "interrupted")
        self.assertEqual(providers.share.await_count, before_calls)
        self.assertTrue((await assets.get(7, identifier))["deliverable"])

    async def test_unexpected_worker_failure_cancels_and_awaits_sibling(self):
        identifier = (await self.create())["id"]
        sibling_started, sibling_cancelled = asyncio.Event(), asyncio.Event()

        async def interrupted(*args):
            target = args[5]
            if target["provider"] == "quark":
                sibling_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    sibling_cancelled.set()
                    raise
            await sibling_started.wait()
            raise RuntimeError("unexpected adapter contract failure")

        providers.upload.side_effect = interrupted
        result = await self.run_job(identifier, "sync")
        self.assertTrue(sibling_cancelled.is_set())
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all(row["status"] == "unknown" for row in result["targets"]))
        self.assertFalse(assets._running)
        replacement = await assets.replace_version(7, identifier, [self.file(b"safe new version")])
        self.assertEqual(replacement["version"], 2)
        self.assertTrue(all(row["status"] == "pending" for row in replacement["targets"]))

    async def test_post_commit_read_failure_preserves_staging_and_database(self):
        with patch.object(assets, "get", AsyncMock(side_effect=RuntimeError("response rendering failed"))):
            with self.assertRaises(RuntimeError):
                await self.create()
        with Session(self.engine) as session:
            persisted = session.scalar(select(CloudAsset))
            self.assertIsNotNone(persisted)
            saved = storage.directory(7, persisted.id, 1) / persisted.manifest[0]["name"]
            self.assertEqual(saved.read_bytes(), b"real local course contents")

    async def test_real_text_analysis_verifies_hash_and_does_not_invent_ai_output(self):
        text = "第一章：实际上传的课程。\n第二章：只提取真实内容。"
        created = await self.create(data=text.encode())
        result = await assets.analyze(7, created["id"])
        self.assertFalse(result["analysis"]["ai_generated"])
        self.assertEqual(result["analysis"]["files"][0]["text"], text)
        saved = storage.directory(7, created["id"], 1) / "lesson.txt"
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        saved.write_bytes(b"tampered")
        with self.assertRaisesRegex(storage.AssetError, "校验失败"):
            await assets.analyze(7, created["id"])

    async def test_failed_upload_size_duplicate_and_filename_validation_leave_no_asset(self):
        cases = [
            [self.file(b"x" * 9)],
            [self.file(filename="Course.txt"), self.file(filename="course.txt")],
            [self.file(filename="../outside.txt")],
        ]
        with patch.dict(os.environ, {"CLOUD_ASSET_MAX_BYTES": "8"}):
            for files in cases:
                with self.subTest(filenames=[file.filename for file in files]):
                    with self.assertRaises(storage.AssetError):
                        await assets.upload(7, "invalid", ["quark"], files)
        self.assertEqual(self.count(CloudAsset), 0)
        self.assertFalse(any(path.is_file() for path in Path(self.temp.name).rglob("*")))

    async def test_private_urls_dns_and_symlinks_are_rejected(self):
        for url in ("http://127.0.0.1/test", "http://169.254.169.254/latest/meta-data/",
                    "http://[::1]/test", "https://localhost/test", "https://a.local/test",
                    "https://user:password@example.com/file", "file:///etc/passwd",
                    "https://example.com:9000/file"):
            with self.subTest(url=url), self.assertRaises(storage.AssetError):
                storage.public_url(url)
        with patch.object(storage.DefaultResolver, "resolve", AsyncMock(return_value=[{"host": "127.0.0.1"}])):
            resolver = storage.PublicResolver()
            try:
                with self.assertRaisesRegex(storage.AssetError, "非公网"):
                    await resolver.resolve("public-looking.example", 443)
            finally:
                await resolver.close()
        base = storage.directory(7, "e" * 32, 1)
        outside = Path(self.temp.name).parent / f"{Path(self.temp.name).name}-outside"
        # Symlink target need not exist: rejection must precede dereference.
        (base / "link.txt").symlink_to(outside)
        with self.assertRaises(storage.AssetError):
            storage.describe(base / "link.txt")


if __name__ == "__main__":
    unittest.main()
