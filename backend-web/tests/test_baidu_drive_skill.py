"""Official CLI contract regression fixtures; no production simulation or cloud mutations."""
from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import UploadFile
from app.services import baidu_drive_cli as cli
from app.services import baidu_drive_skill_auth as auth
from app.services import baidu_drive_skill as drive
from app.services.baidu_drive_auth import BaiduDriveError, _locks, write_private

ROW = {"fs_id": 9876543210123456789, "path": "/apps/bdpan/说明.txt", "server_filename": "说明.txt",
       "size": 12, "isdir": False, "server_mtime": "2026-09-03T10:23:56+08:00",
       "return_url": "https://pan.baidu.com/union/spirit/launch?target=file&opaque=a%2Fb"}

FAKE_CLI = '''
import sys,json,time
from pathlib import Path
args=sys.argv[1:]; config=Path(args[args.index('--config-path')+1]); json_mode='--json' in args
for key in ['--config-path','--no-check-update','--json']:
 if key in args:
  n=args.index(key); del args[n:n+(2 if key=='--config-path' else 1)]
cmd=args[0]
if cmd=='version': print('3.8.7')
elif cmd=='whoami':
 logged=config.exists()
 data={'authenticated':logged,'has_valid_token':logged,'username':'测试账号','expires_at':'2050-01-01T00:00:00Z'}
 print(json.dumps(data) if json_mode else ('已登录' if logged else '未登录'))
elif cmd=='login':
 if '--help' in args: print('--get-auth-url --set-code-stdin --accept-disclaimer')
 elif '--get-auth-url' in args: print('https://openapi.baidu.com/oauth/2.0/authorize?response_type=code&redirect_uri=oob')
 elif '--set-code-stdin' in args:
  code=sys.stdin.readline().strip()
  if code=='c'*32:time.sleep(5)
  if code!='a'*32:sys.exit(1)
  config.write_bytes(b'opaque-test-credential-do-not-expose')
  print('已登录')
 else:sys.exit(9)
elif cmd=='logout':config.unlink(missing_ok=True);print('{}')
elif cmd=='ls':print('[]' if config.exists() else '{"code":1,"error":"unauthorized"}')
elif cmd=='slow':time.sleep(5);print('{}')
else:sys.exit(8)
'''


class SkillAuth(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        executable = root / "bdpan"
        executable.write_text("#!" + sys.executable + "\n" + FAKE_CLI)
        executable.chmod(0o700)
        self.env = patch.dict(os.environ, {"BAIDU_AUTH_DIR": str(root / "auth"), "BAIDU_BDPAN_BIN": str(executable)})
        self.env.start()
        _locks.clear()
        auth._sessions.clear()

    async def asyncTearDown(self):
        await auth.shutdown()
        auth._sessions.clear()
        _locks.clear()
        self.env.stop()
        self.temp.cleanup()

    async def login(self, user=17):
        start = await auth.start_authorization(user, True)
        status = await auth.complete_authorization(user, "a" * 32, "authorization_code", start["session_id"])
        return start, status

    async def test_official_script_stdin_and_private_persistence(self):
        start, status = await self.login()
        self.assertTrue(status["connected"] and status["verified"])
        self.assertEqual(status["account_id"], start["session_id"])
        self.assertNotIn("opaque-test-credential", json.dumps(status))
        again = await auth.get_status(17)
        self.assertEqual(again["account_id"], status["account_id"])
        self.assertFalse((await auth.get_status(18))["connected"])
        self.assertEqual(stat.S_IMODE(cli.config_path(17).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(cli.config_path(17).parent.stat().st_mode), 0o700)

    async def test_failed_switch_preserves_valid_connection(self):
        _, previous = await self.login()
        old_path = cli.config_path(17)
        start = await auth.start_authorization(17, True)
        with self.assertRaises(BaiduDriveError):
            await auth.complete_authorization(17, "b" * 32, "authorization_code", start["session_id"])
        self.assertEqual(cli.config_path(17), old_path)
        self.assertTrue((await auth.get_status(17))["connected"])
        self.assertEqual(auth.account(17)["account_id"], previous["account_id"])

    async def test_user_confirm_required_and_cross_user_rejected(self):
        with self.assertRaises(BaiduDriveError):
            await auth.start_authorization(17, False)
        start = await auth.start_authorization(17, True)
        with self.assertRaises(BaiduDriveError):
            await auth.session_status(18, start["session_id"])
        with self.assertRaises(BaiduDriveError):
            await auth.complete_authorization(18, "a" * 32, "authorization_code", start["session_id"])
        self.assertEqual((await auth.cancel(17, start["session_id"]))["status"], "cancelled")
        self.assertFalse(auth._sessions[start["session_id"]].directory.exists())

    async def test_expired_code_not_submitted(self):
        start = await auth.start_authorization(17, True)
        session = auth._sessions[start["session_id"]]
        session.expires_at = 1
        with self.assertRaises(BaiduDriveError):
            await auth.complete_authorization(17, "a" * 32, "authorization_code", session.id)
        self.assertFalse(session.submitted)

    async def test_missing_session_cancel_is_idempotent_after_restart(self):
        result = await auth.cancel(17, "b" * 32)
        self.assertEqual(result["status"], "cancelled")

    async def test_logout_clears_active_credential(self):
        await self.login()
        configuration = cli.config_path(17)
        await auth.logout(17)
        self.assertFalse(configuration.exists())
        self.assertFalse((await auth.get_status(17))["connected"])

    async def test_timeout_terminates_official_child(self):
        with self.assertRaisesRegex(BaiduDriveError, "超时"):
            await cli.run(17, "slow", timeout=0.05)

    async def test_busy_operations_block_account_switch(self):
        async with _locks.setdefault(17, asyncio.Lock()):
            with self.assertRaisesRegex(BaiduDriveError, "执行"):
                await auth.start_authorization(17, True)

    async def test_cancel_interrupts_code_exchange_without_activating(self):
        start = await auth.start_authorization(17, True)
        session = auth._sessions[start["session_id"]]
        request = asyncio.create_task(auth.complete_authorization(17, "c" * 32, "authorization_code", session.id))
        while not session.submitted:
            await asyncio.sleep(0.01)
        response = await asyncio.wait_for(auth.cancel(17, session.id), 1)
        self.assertEqual(response["status"], "cancelled")
        with self.assertRaises(BaiduDriveError):
            await request
        self.assertIsNone(auth.account(17))

    async def test_legacy_tokens_do_not_enable_skill_connection(self):
        write_private(cli.user_dir(17) / "account.json", {"access_token": "legacy-secret"})
        status = await auth.get_status(17)
        self.assertFalse(status["connected"])
        self.assertNotIn("legacy-secret", json.dumps(status))

    async def test_provider_failure_keeps_saved_connection_but_unverified(self):
        await self.login()
        original = cli.run
        async def failure(user, *args, **kwargs):
            if args[0] == "ls":
                raise BaiduDriveError("provider unavailable")
            return await original(user, *args, **kwargs)
        with patch.object(cli, "run", failure):
            result = await auth.get_status(17)
        self.assertTrue(result["connected"])
        self.assertFalse(result["verified"])
        self.assertIsNotNone(auth.account(17))


class SkillOperations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"BAIDU_AUTH_DIR": self.temp.name})
        self.env.start()
        _locks.clear()
        self.record = {"account_id": "a" * 32, "nickname": "测试账号"}
        cli.private_directory(cli.user_dir(17) / "official/connections" / self.record["account_id"])
        self.auth = patch.object(auth, "require_account", AsyncMock(return_value=self.record))
        self.auth.start()

    def tearDown(self):
        self.auth.stop()
        self.env.stop()
        _locks.clear()
        self.temp.cleanup()

    def test_scope_traversal_names_and_trusted_links(self):
        self.assertEqual(drive.validate_path("/"), cli.ROOT)
        for value in ["/apps/other/f", "/apps/bdpan/../f", "/apps/bdpan2/f", "/apps/bdpan/./f", "/apps/bdpan//f", "/apps/bdpan/~/f"]:
            with self.assertRaises(BaiduDriveError):
                drive.validate_path(value)
        with self.assertRaises(BaiduDriveError):
            drive.validate_path("/", allow_root=False)
        for value in ["../x", "a/b", "x\x00", ".."]:
            with self.assertRaises(BaiduDriveError):
                drive.validate_name(value)
        for value in ["https://baidu.com.evil.test/a", "http://pan.baidu.com/a", "https://pan.baidu.com:444/a", "https://u:p@pan.baidu.com/a"]:
            with self.assertRaises(BaiduDriveError):
                cli.trusted_url(value)

    def test_large_ids_time_and_opaque_links(self):
        row = drive.file_view(ROW)
        self.assertEqual(row["fs_id"], "9876543210123456789")
        self.assertEqual(row["return_url"], ROW["return_url"])
        self.assertGreater(row["updated_at"], 1700000000000)
        self.assertEqual(row["category"], 4)
        self.assertEqual(drive.file_view({**ROW, "path": "我的应用数据/bdpan/说明.txt"})["path"], ROW["path"])

    async def test_list_pagination_and_cache(self):
        with patch.object(cli, "run", AsyncMock(return_value=[ROW, {**ROW, "fs_id": 123, "path": "/apps/bdpan/b.txt"}])) as call:
            result = await drive.list_files(17, page_size=1)
        self.assertTrue(result["has_more"])
        self.assertEqual(len(result["files"]), 1)
        self.assertIn("--limit", call.call_args.args)
        self.assertNotIn(cli.ROOT, call.call_args.args)
        self.assertNotIn("--", call.call_args.args)

    async def test_list_child_directory_uses_documented_relative_cli_path(self):
        with patch.object(cli, "run", AsyncMock(return_value=[])) as call:
            await drive.list_files(17, "/apps/bdpan/子目录")
        self.assertEqual(call.call_args.args[-2:], ("--", "子目录"))

    async def test_search_uses_official_50_limit_and_global_scope(self):
        with patch.object(cli, "run", AsyncMock(return_value={"total": 101, "items": [ROW]})) as call:
            result = await drive.list_files(17, "/apps/bdpan/子目录", "测试", 2, 100)
        self.assertEqual(result["page_size"], 50)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["dir"], cli.ROOT)
        self.assertEqual(result["search_scope"], "application")
        self.assertNotIn("子目录", str(call.call_args))

    async def test_confirm_and_unsupported_fail_before_call(self):
        with patch.object(cli, "run", AsyncMock()) as call:
            for op, payload in [("delete", {"paths": [ROW["path"]]}), ("semantic-search", {"query": "x"}), ("upload-url", {"confirmed": True})]:
                with self.assertRaises(BaiduDriveError):
                    await drive.run_operation(17, op, payload)
            call.assert_not_awaited()

    async def test_move_collision_does_not_overwrite(self):
        with patch.object(cli, "run", AsyncMock(return_value=[ROW])) as call:
            result = await drive.run_operation(17, "move", {"paths": [ROW["path"]], "target_dir": "/", "confirmed": True})
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all(args.args[1] == "ls" for args in call.await_args_list))

    async def test_bulk_mutation_reports_partial_failure(self):
        other = {**ROW, "fs_id": 2, "path": "/apps/bdpan/b.txt", "server_filename": "b.txt"}
        with patch.object(cli, "run", AsyncMock(side_effect=[[ROW, other], {"status": "ok", "count": 1}, BaiduDriveError("provider rejected")])):
            result = await drive.run_operation(17, "delete", {"paths": [ROW["path"], other["path"]], "confirmed": True})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["results"]), 2)

    async def test_share_generated_pwd_and_connection_history(self):
        with patch.object(cli, "run", AsyncMock(side_effect=[[ROW], {"link": "https://pan.baidu.com/s/example", "pwd": "ab12"}])):
            share = await drive.create_share(17, [], "资料", paths=[ROW["path"]])
        self.assertTrue(share["saved"])
        self.assertEqual(share["share"]["passcode"], "ab12")
        self.assertEqual((await drive.list_shares(17))["total"], 1)
        with patch.object(auth, "require_account", AsyncMock(return_value={"account_id": "b" * 32})):
            self.assertEqual((await drive.list_shares(17))["total"], 0)
        with self.assertRaises(BaiduDriveError):
            await drive.create_share(17, [], "资料", password="abcd", paths=[ROW["path"]])

    async def test_stale_fileid_does_not_share_changed_file(self):
        drive._cache(17, self.record, [drive.file_view(ROW)])
        with patch.object(cli, "run", AsyncMock(return_value=[{**ROW, "fs_id": 3}])) as call:
            with self.assertRaisesRegex(BaiduDriveError, "变化"):
                await drive.create_share(17, [str(ROW["fs_id"])], "资料")
        self.assertEqual(call.await_count, 1)

    async def test_upload_uses_real_result_and_never_exposes_local_paths(self):
        with patch.object(cli, "config_path", return_value=Path(self.temp.name) / "config.json"), patch.object(cli, "run", AsyncMock(side_effect=[[], {"fsid": "12", "local": "private-local-path", "message": "上传成功"}])):
            result = await drive.upload_files(17, [UploadFile(filename="资料.txt", file=io.BytesIO(b"text"))])
        self.assertEqual(result["status"], "success")
        self.assertNotIn("private-local-path", json.dumps(result))

    async def test_transfer_listing_uses_official_items_contract(self):
        with patch.object(cli, "run", AsyncMock(return_value={"items": [{"fs_id": "123", "name": "资料.txt", "path": "/资料.txt", "is_dir": False, "size": 12}], "has_more": False, "page": 1, "page_size": 100})):
            result = await drive.run_operation(17, "share-transfer-list", {"url": "https://pan.baidu.com/s/example"})
        self.assertEqual(result["files"][0]["fs_id"], "123")
        self.assertEqual(result["files"][0]["filename"], "资料.txt")

    async def test_transfer_partial_is_not_reported_as_complete(self):
        with patch.object(cli, "run", AsyncMock(return_value={"saved_path": "我的应用数据/bdpan/", "count": 1, "files": [], "partial": True, "skipped_count": 2})):
            result = await drive.run_operation(17, "share-transfer", {"url": "https://pan.baidu.com/s/example", "confirmed": True})
        self.assertEqual(result["status"], "partial")
        self.assertIn("部分完成", result["message"])

    async def test_select_submitted_does_not_mean_complete(self):
        with patch.object(cli, "run", AsyncMock(return_value={"status": "submitted", "task_id": "official-task", "selected_count": 1, "target_dir": "/apps/bdpan", "saved_path": "我的应用数据/bdpan/"})):
            result = await drive.run_operation(17, "share-transfer-select", {"url": "https://pan.baidu.com/s/example", "fsids": ["123"], "confirmed": True})
        self.assertEqual(result["status"], "submitted")
        self.assertIn("已提交", result["message"])

    async def test_folder_named_download_zip_downloads_without_collision(self):
        metadata = {**drive.file_view(ROW), "isdir": True, "filename": "download.zip", "size": 0}
        async def save(user, *args, **kwargs):
            target = Path(args[-1])
            target.mkdir()
            (target / "safe.txt").write_text("text")
            return {"fsid": "1"}
        with patch.object(drive, "_metadata", AsyncMock(return_value=metadata)), patch.object(cli, "run", save):
            file, name, directory = await drive.download(17, "/apps/bdpan/download.zip")
        try:
            self.assertTrue(file.is_file())
            self.assertEqual(name, "download.zip.zip")
        finally:
            import shutil
            shutil.rmtree(directory)

    async def test_completed_folder_over_limit_rejected(self):
        metadata = {**drive.file_view(ROW), "isdir": True, "filename": "folder", "size": 0}
        async def save(user, *args, **kwargs):
            target = Path(args[-1])
            target.mkdir()
            (target / "large.txt").write_bytes(b"x" * 20)
            return {"fsid": "1"}
        with patch.dict(os.environ, {"BAIDU_MAX_DOWNLOAD_BYTES": "10"}), patch.object(drive, "_metadata", AsyncMock(return_value=metadata)), patch.object(cli, "run", save):
            with self.assertRaisesRegex(BaiduDriveError, "超过"):
                await drive.download(17, "/apps/bdpan/folder")

    def test_cli_envelope_error_is_redacted(self):
        with self.assertRaises(BaiduDriveError) as error:
            cli._parse(b'{"code":42,"error":"access_token=private"}', 0)
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(cli._parse(b'{"code":0,"data":{"fsid":"1"},"error":""}', 0), {"fsid": "1"})
        with self.assertRaises(BaiduDriveError):
            cli._parse(b'{"success":false,"message":"secret"}', 0)


class UvicornLoopCompatibility(unittest.TestCase):
    def test_uvloop_runs_official_script_and_file_commands(self):
        import uvloop
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "bdpan"
            executable.write_text("#!" + sys.executable + "\n" + FAKE_CLI)
            executable.chmod(0o700)
            with patch.dict(os.environ, {"BAIDU_AUTH_DIR": str(root / "auth"), "BAIDU_BDPAN_BIN": str(executable)}):
                _locks.clear()
                auth._sessions.clear()
                async def exercise():
                    try:
                        result = await auth.start_authorization(17, True)
                        self.assertEqual(result["status"], "waiting")
                        status = await auth.complete_authorization(17, "a" * 32, "authorization_code", result["session_id"])
                        self.assertTrue(status["connected"] and status["verified"])
                        self.assertEqual(stat.S_IMODE(cli.config_path(17).stat().st_mode), 0o600)
                    finally:
                        await auth.shutdown()
                try:
                    uvloop.run(exercise())
                finally:
                    auth._sessions.clear()
                    _locks.clear()

    def test_private_child_mask_and_literal_arguments_under_uvloop(self):
        import uvloop
        arguments = ["中文 文件名", "$(echo must-stay-literal)", "a;b&c"]
        async def exercise():
            process = await cli.spawn(sys.executable, "-c",
                "import os,sys,json; print(json.dumps({'mask':os.umask(0),'args':sys.argv[1:]}))", *arguments,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            stdout, _ = await process.communicate()
            self.assertEqual(process.returncode, 0)
            result = json.loads(stdout)
            self.assertEqual(result["mask"], 0o077)
            self.assertEqual(result["args"], arguments)
        uvloop.run(exercise())


if __name__ == "__main__":
    unittest.main()
