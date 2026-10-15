"""OAuth lifecycle and account switching regressions; no provider mutations."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import quark_drive_auth as auth, quark_drive_sessions as sessions
from app.services import quark_drive as drive, quark_drive_jobs as jobs


class AuthorizationSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await sessions.stop_sessions()
        sessions._sessions.clear()
        sessions._session_gates.clear()
        auth._user_locks.clear()

    async def test_official_wait_can_be_cancelled_and_belongs_only_to_its_owner(self):
        cancelled = asyncio.Event()
        async def provider(_user, _command, **options):
            await options['on_event']({'type': 'authorization', 'data': {
                'authorization_url': 'https://pan.quark.cn/open/v1/oauth/agent?page_code=test-only'}})
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with patch.object(sessions, '_assert_no_jobs', AsyncMock()), patch.object(sessions, '_run_cli', provider):
            result = await sessions.start_session(17)
            self.assertEqual(result['status'], 'waiting')
            self.assertGreater(result['expires_at'], 0)
            with self.assertRaises(auth.QuarkAuthError):
                await sessions.get_session(18, result['session_id'])
            with self.assertRaises(auth.QuarkAuthError):
                await sessions.cancel_session(18, result['session_id'])
            self.assertFalse(cancelled.is_set())
            result = await sessions.cancel_session(17, result['session_id'])
            self.assertEqual(result['status'], 'cancelled')
            self.assertTrue(cancelled.is_set())

    async def test_provider_failure_cannot_be_reported_as_authorized(self):
        with patch.object(sessions, '_assert_no_jobs', AsyncMock()), patch.object(
            sessions, '_run_cli', AsyncMock(return_value={'code': -103})):
            with self.assertRaises(auth.QuarkAuthError):
                await sessions.start_session(17)
        self.assertTrue(all(row.status == 'failed' for row in sessions._sessions.values()))

    async def test_refresh_retires_previous_wait(self):
        async def provider(_user, _command, **options):
            await options['on_event']({'type': 'authorization', 'data': {
                'authorization_url': 'https://pan.quark.cn/open/v1/oauth/agent?page_code=test-only'}})
            await asyncio.Event().wait()
        with patch.object(sessions, '_assert_no_jobs', AsyncMock()), patch.object(sessions, '_run_cli', provider):
            first = await sessions.start_session(17)
            second = await sessions.start_session(17)
            self.assertNotEqual(first['session_id'], second['session_id'])
            self.assertEqual((await sessions.get_session(17, first['session_id']))['status'], 'cancelled')
            self.assertEqual((await sessions.get_session(17, second['session_id']))['status'], 'waiting')


class AccountIsolationTests(unittest.TestCase):
    def test_switching_accounts_preserves_but_separates_existing_share_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'QUARK_AUTH_DIR': directory}):
            config = auth._user_dir(17) / 'skill/codex/config.json'
            config.parent.mkdir(parents=True)
            def select(identity):
                config.write_text(json.dumps({'currentUserId': identity, identity: {'refreshToken': 'test-only'}}))
            select('account-a')
            auth._record_legacy_account(17)
            drive._write_shares(17, [{'id': 'share-a'}])
            key = auth._account_key(17)
            select('account-b')
            self.assertEqual(drive._read_shares(17), [])
            drive._write_shares(17, [{'id': 'share-b'}])
            with self.assertRaises(auth.QuarkAuthError):
                jobs._check_job_account(17, {'_account_id': key})
            with self.assertRaises(auth.QuarkAuthError):
                jobs._check_job_account(17, {})  # legacy task still belongs to A
            select('account-a')
            self.assertEqual(drive._read_shares(17), [{'id': 'share-a'}])
            jobs._check_job_account(17, {'_account_id': key})
            jobs._check_job_account(17, {})
            self.assertNotIn('account-a', key)
