"""Upstream merge regressions. Run only in an isolated cloud test container."""
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from starlette.requests import Request

from test_mcp_gateway import Db
from app.api import deps
from app.api.routes import db_backup_logs, email_logs, user_login_logs
from app.services.db_backup_log_service import DbBackupDeleteError, DbBackupLogService
from app.services.email_log_service import EmailLogService
from app.services.user_login_log_service import UserLoginLogService
from common.models.db_backup_log import DbBackupLog
from common.models.data_dictionary import XYDataDictionary
from common.models.email_log import XYEmailLog
from common.models.user_login_log import XYUserLoginLog
from common.models.user import UserRole, UserStatus
from common.services.email_log_service import record_email_log
from common.utils.http_utils import get_client_ip


class MutableDb(Db):
    async def delete(self, row):
        self.db.delete(row)


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'backups'
        self.root.mkdir()
        self.engine = create_engine('sqlite://')
        for model in [DbBackupLog, XYDataDictionary, XYEmailLog, XYUserLoginLog]:
            model.__table__.create(self.engine)
        self.db = MutableDb(self.engine)
        self.service = DbBackupLogService(self.db)
        self.root_patch = patch('common.utils.backup_paths.get_backup_root', return_value=self.root)
        self.service_patch = patch('app.services.db_backup_log_service.get_backup_root', return_value=self.root)
        self.root_patch.start()
        self.service_patch.start()

    async def asyncTearDown(self):
        self.service_patch.stop()
        self.root_patch.stop()
        self.db.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    @asynccontextmanager
    async def audit_db(self):
        async with MutableDb(self.engine) as db:
            yield db

    async def backup(self, key, path=None, missing=False):
        path = path or self.root / f'{key}.sql.gz'
        if not missing:
            path.write_bytes(b'isolated backup fixture')
        self.db.add(DbBackupLog(id=key, status='success', file_name=path.name, file_path=str(path),
                               created_at=datetime(2026, 10, 25, 23, 59, 59)))
        await self.db.commit()
        return path

    async def test_backup_delete_deduplicates_and_removes_file(self):
        path = await self.backup(1)
        deleted, failed, _ = await self.service.delete_logs([1, 1])
        self.assertEqual((deleted, failed), (1, []))
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIsNone(await self.db.get(DbBackupLog, 1))

    async def test_missing_file_does_not_prevent_removing_log(self):
        await self.backup(1, missing=True)
        self.assertEqual((await self.service.delete_logs([1]))[:2], (1, []))

    async def test_outside_file_preserved_and_partial_result_explicit(self):
        safe = await self.backup(1)
        outside = await self.backup(2, Path(self.tmp.name) / 'outside.sql.gz')
        deleted, failed, _ = await self.service.delete_logs([1, 2, 99])
        self.assertEqual(deleted, 1)
        self.assertEqual(set(failed), {2, 99})
        self.assertFalse(safe.exists())
        self.assertTrue(outside.exists())
        self.assertIsNotNone(await self.db.get(DbBackupLog, 2))

    async def test_rollback_restores_file_and_record(self):
        path = await self.backup(1)
        with patch.object(self.db, 'commit', AsyncMock(side_effect=RuntimeError('fixture commit failed'))):
            with self.assertRaises(DbBackupDeleteError):
                await self.service.delete_logs([1])
        self.assertTrue(path.exists())
        self.assertIsNotNone(await self.db.get(DbBackupLog, 1))
        self.assertFalse(list(self.root.glob('.*.deleting')))

    async def test_lost_commit_response_detects_committed_delete(self):
        path = await self.backup(1)
        async def committed_but_response_lost():
            self.db.db.commit()
            raise RuntimeError('fixture connection lost after commit')
        with patch.object(self.db, 'commit', committed_but_response_lost):
            result = await self.service.delete_logs([1])
        self.assertEqual(result[:2], (1, []))
        self.assertFalse(path.exists())
        self.assertFalse(list(self.root.glob('.*.deleting')))

    async def test_uncertain_commit_preserves_staged_file(self):
        await self.backup(1)
        with patch.object(self.db, 'commit', AsyncMock(side_effect=RuntimeError('fixture offline'))), \
             patch.object(self.service, '_get_existing_log_ids', AsyncMock(side_effect=RuntimeError('fixture offline'))):
            with self.assertRaises(DbBackupDeleteError):
                await self.service.delete_logs([1])
        self.assertEqual(len(list(self.root.glob('.*.deleting'))), 1)
        await self.service.recover_staged_files()
        self.assertTrue((self.root / '1.sql.gz').exists())

    async def test_crash_recovery_restores_uncommitted_and_finishes_committed(self):
        path = await self.backup(1)
        _, staged = self.service._stage_file_for_delete(path, 1)
        orphan = self.root / ('.old.sql.gz.999.' + 'a' * 32 + '.deleting')
        orphan.write_bytes(b'fixture')
        unknown = self.root / '.unrecognized.deleting'
        unknown.write_bytes(b'keep')
        await self.service.recover_staged_files()
        self.assertTrue(path.exists())
        self.assertFalse(staged.exists() or orphan.exists())
        self.assertTrue(unknown.exists())

    async def test_download_open_handle_survives_concurrent_delete(self):
        await self.backup(1)
        response = await db_backup_logs.download_db_backup_file(1, SimpleNamespace(id=1), self.service)
        await self.service.delete_logs([1])
        chunks = [chunk async for chunk in response.body_iterator]
        self.assertEqual(b''.join(chunks), b'isolated backup fixture')

    async def test_date_filter_includes_end_day_and_rejects_invalid_range(self):
        await self.backup(1)
        self.assertEqual((await self.service.list_logs(end_date='2026-10-25'))[1], 1)
        with self.assertRaises(ValueError):
            await self.service.list_logs(start_date='2026-10-26', end_date='2026-10-25')
        with self.assertRaises(ValueError):
            await self.service.list_logs(start_date='invalid')

    async def test_batch_api_validates_input_and_returns_partial_counts(self):
        app = FastAPI()
        app.include_router(db_backup_logs.router)
        app.dependency_overrides[deps.get_current_admin_user] = lambda: SimpleNamespace(id=1)
        app.dependency_overrides[deps.get_db_backup_log_service] = lambda: self.service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            for ids in [[], [True], [0], [2**63], ['1'], list(range(1, 102))]:
                result = (await client.post('/db-backup-logs/batch-delete', json={'ids': ids})).json()
                self.assertFalse(result['success'])
                self.assertEqual(result['code'], 40001)
            await self.backup(1)
            result = (await client.post('/db-backup-logs/batch-delete', json={'ids': [1, 99]})).json()
            self.assertFalse(result['success'])
            self.assertEqual(result['data'], {'deleted': 1, 'failed_ids': [99]})

    async def test_admin_routes_reject_regular_user_and_anonymous(self):
        app = FastAPI()
        for router in [db_backup_logs.router, email_logs.router, user_login_logs.router]:
            app.include_router(router)
        async def session():
            yield self.db
        app.dependency_overrides[deps.get_db_session] = session
        urls = ['/user-login-logs', '/email-logs', '/email-logs/options', '/db-backup-logs']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            for url in urls:
                self.assertEqual((await client.get(url)).status_code, 401)
            app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(role=UserRole.MEMBER, status=UserStatus.ACTIVE)
            for url in urls:
                self.assertEqual((await client.get(url)).status_code, 403)

    async def test_login_audit_records_both_outcomes_and_filters(self):
        service = UserLoginLogService(self.db)
        with patch('app.services.user_login_log_service.async_session_maker', self.audit_db):
            for success in [True, False]:
                await service.record(login_method='username_password', success=success, login_account='fixture-user',
                                     client_ip='203.0.113.1', owner_id=1 if success else None)
        rows, total = await service.list_logs(keyword='fixture', limit=1)
        self.assertEqual((len(rows), total), (1, 2))
        self.assertFalse(rows[0]['success'])
        self.assertNotIn('password', rows[0])
        self.assertEqual((await service.list_logs(login_method='email_code'))[1], 0)

    async def test_audit_failure_leaves_login_transaction_untouched(self):
        pending = XYUserLoginLog(login_method='username_password', success=True, login_account='keep-pending')
        self.db.add(pending)
        with patch('app.services.user_login_log_service.async_session_maker', side_effect=RuntimeError('fixture offline')), \
             patch.object(self.db, 'rollback', AsyncMock()) as rollback:
            await UserLoginLogService(self.db).record(login_method='username_password', success=True)
            rollback.assert_not_called()
        self.assertIn(pending, self.db.db.new)

    async def test_email_audit_is_bounded_and_filters(self):
        with patch('common.services.email_log_service.async_session_maker', self.audit_db):
            self.assertTrue(await record_email_log(recipient_email='fixture@example.invalid',
                trigger_operation='test_email', success=False, result_message='x' * 10000))
        service = EmailLogService(self.db)
        result = await service.list_logs(send_result='failure', recipient_email='fixture', page_size=1)
        self.assertEqual(result['total'], 1)
        row = (await self.db.execute(select(XYEmailLog))).scalar_one()
        self.assertEqual(len(row.result_message), 4096)
        self.assertFalse({'body', 'verification_code', 'password'} & set(row.__table__.columns.keys()))
        with self.assertRaises(ValueError):
            await service.list_logs(send_result='wrong')

    async def test_email_failure_never_raises_to_caller(self):
        with patch('common.services.email_log_service.async_session_maker', side_effect=RuntimeError('fixture offline')), \
             patch('common.services.email_log_service.asyncio.sleep', AsyncMock()):
            self.assertFalse(await record_email_log(recipient_email='fixture@example.invalid', trigger_operation='test_email', success=False))


class AuditIpTests(unittest.TestCase):
    def request(self, peer, headers):
        return Request({'type': 'http', 'client': (peer, 1234), 'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    def test_untrusted_client_cannot_forge_ip(self):
        with patch.dict(os.environ, {'TRUSTED_PROXY_IPS': '127.0.0.1'}):
            self.assertEqual(get_client_ip(self.request('203.0.113.9', {'X-Real-IP': '1.1.1.1'})), '203.0.113.9')

    def test_trusted_nginx_uses_overwritten_real_ip(self):
        with patch.dict(os.environ, {'TRUSTED_PROXY_IPS': '172.18.0.1'}):
            self.assertEqual(get_client_ip(self.request('172.18.0.1', {'X-Real-IP': '203.0.113.9', 'X-Forwarded-For': '1.1.1.1, 203.0.113.9'})), '203.0.113.9')
            self.assertEqual(get_client_ip(self.request('172.18.0.1', {'X-Forwarded-For': '1.1.1.1, 203.0.113.9'})), '203.0.113.9')

    def test_invalid_headers_fall_back_to_peer(self):
        self.assertEqual(get_client_ip(self.request('127.0.0.1', {'X-Real-IP': 'invalid'})), '127.0.0.1')
