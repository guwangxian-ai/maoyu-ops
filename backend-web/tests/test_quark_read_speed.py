"""Read optimization must retain complete output, exit and account serialization."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services import quark_drive_auth as auth


class ReadRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = patch.dict('os.environ', {'QUARK_AUTH_DIR': str(self.root)})
        self.state.start()
        self.addCleanup(self.state.stop)
        self.prepare = patch.object(auth, '_prepare_user_runtime', return_value=(Path('/node'), Path('/quark-drive.cjs'), self.root, {}))
        self.prepare.start()
        self.addCleanup(self.prepare.stop)

    def stream(self, events, returncode=0):
        stream = asyncio.StreamReader()
        stream.feed_data(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
        stream.feed_eof()
        return SimpleNamespace(stdout=stream, returncode=returncode, wait=AsyncMock())

    async def test_complete_output_and_actual_process_exit_are_both_required(self):
        stream = asyncio.StreamReader()
        seen_artifact, exited, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def wait():
            waiting.set()
            await exited.wait()

        process = SimpleNamespace(stdout=stream, returncode=0, wait=wait)

        async def observe(event):
            if event.get('type') == 'artifact':
                seen_artifact.set()

        events = [{'type': 'list', 'data': {'fid': 'one'}},
                  {'type': 'result', 'action': 'browse', 'code': 0, 'data': {}},
                  {'type': 'artifact', 'data': {'file_path': '/private/files.jsonl', 'count': 1}}]
        with patch.object(auth.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            pending = asyncio.create_task(auth._run_cli(17, 'browse', fast_read=False, on_event=observe))
            stream.feed_data(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
            await seen_artifact.wait()
            self.assertFalse(pending.done())
            stream.feed_eof()
            await waiting.wait()
            self.assertFalse(pending.done())
            exited.set()
            result = await pending
        self.assertEqual(result['_items'], [{'fid': 'one'}])
        self.assertEqual(result['_artifact']['count'], 1)

    async def test_success_output_with_abnormal_exit_is_rejected(self):
        process = self.stream([{'type': 'result', 'code': 0, 'data': {}}], returncode=1)
        with patch.object(auth.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            with self.assertRaisesRegex(auth.QuarkAuthError, '未正常完成'):
                await auth._run_cli(17, 'get-user-info', fast_read=False)

    async def test_later_failure_result_is_not_overwritten_by_earlier_success(self):
        process = self.stream([{'type': 'result', 'code': 0, 'data': {}},
                               {'type': 'result', 'code': -103, 'data': {}}], returncode=1)
        with patch.object(auth.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
            result = await auth._run_cli(17, 'browse', fast_read=False)
        self.assertEqual(result['code'], -103)

    async def test_preload_is_limited_to_read_commands_and_can_be_disabled(self):
        for command, enabled in [('browse', True), ('search', True), ('get-user-info', True), ('move', True), ('browse', False)]:
            with self.subTest(command=command, enabled=enabled):
                process = self.stream([{'type': 'result', 'code': 0, 'data': {}}])
                with patch.object(auth.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
                    await auth._run_cli(17, command, fast_read=enabled)
                self.assertEqual(str(auth.READ_BRIDGE) in spawn.call_args.args, enabled and command in auth.FAST_READ_COMMANDS)

    async def test_authorization_keeps_its_existing_browser_bridge(self):
        process = self.stream([{'type': 'result', 'code': 0, 'data': {}}])
        with patch.object(auth.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            await auth._run_cli(17, 'login', browser_bridge=True, fast_read=True)
        self.assertIn(str(auth.LOGIN_BRIDGE), spawn.call_args.args)
        self.assertNotIn(str(auth.READ_BRIDGE), spawn.call_args.args)


class ReadQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = patch.dict(auth._user_locks, {}, clear=True)
        self.state.start()
        self.addCleanup(self.state.stop)

    async def test_read_waits_for_short_operation_without_overlapping(self):
        lock = auth._user_locks.setdefault(17, asyncio.Lock())
        await lock.acquire()
        entered = asyncio.Event()

        async def reader():
            async with auth._read_lock(17):
                entered.set()
                self.assertTrue(lock.locked())

        pending = asyncio.create_task(reader())
        await asyncio.sleep(0)
        self.assertFalse(entered.is_set())
        lock.release()
        await pending
        self.assertTrue(entered.is_set())
        self.assertFalse(lock.locked())

    async def test_long_task_timeout_does_not_release_its_lock(self):
        lock = auth._user_locks.setdefault(17, asyncio.Lock())
        await lock.acquire()
        try:
            with self.assertRaises(auth.QuarkBusyError):
                async with auth._read_lock(17, timeout=.01):
                    self.fail('read must not enter a locked account')
            self.assertTrue(lock.locked())
        finally:
            lock.release()

    async def test_cancelled_waiter_does_not_leak_or_release_another_owner(self):
        lock = auth._user_locks.setdefault(17, asyncio.Lock())
        await lock.acquire()

        async def reader():
            async with auth._read_lock(17):
                self.fail('cancelled waiter must not enter')

        pending = asyncio.create_task(reader())
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(lock.locked())
        lock.release()
        async with auth._read_lock(17):
            self.assertTrue(lock.locked())
        self.assertFalse(lock.locked())


if __name__ == '__main__':
    unittest.main()
