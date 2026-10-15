"""Private process and QR regressions. Provider fixtures are confined to tests."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import baidu_pcs_auth as auth
from app.services import baidu_pcs_cli as cli
from app.services.baidu_drive_auth import BaiduDriveError, BaiduTokenExpired, _locks, redact, write_private

SECRET = "b" * 64
CREDS = {"bduss": SECRET, "stoken": "netdisk-test-token", "cookies": "BDUSS=" + SECRET, "uid": "12345"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK1kAAAAASUVORK5CYII=")
FAKE_BRIDGE = '''
import json,os,sys,time
from pathlib import Path
r=json.load(sys.stdin);op=r['operation'];args=r['args']
if op=='slow':
 Path(args['pid']).write_text(str(os.getpid()));time.sleep(15)
elif op=='overflow':
 sys.stdout.write('x'*(16*1024**2+1));sys.exit()
elif op=='invalid_json':print('not-json');sys.exit()
elif op=='bad_schema':print('{}');sys.exit()
elif op=='expired':
 print(json.dumps({'success':False,'error_code':'authentication_required','kind':'auth','message':r['credentials']['bduss']}));sys.exit(1)
elif op=='error':
 print(json.dumps({'success':False,'error_code':'unknown','kind':'provider','provider_code':12,'message':r['credentials']['bduss']}));sys.exit()
elif op=='disk':Path(args['file']).write_bytes(b'x'*1024)
print(json.dumps({'success':True,'data':{'operation':op,'args':args,'argv_count':len(sys.argv),'env_credential':os.getenv('BDUSS')}}))
'''


class PCSProcess(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        executable = self.root / "bridge"
        executable.write_text("#!" + sys.executable + "\n" + FAKE_BRIDGE)
        executable.chmod(0o700)
        self.env = patch.dict(os.environ, {"BAIDU_AUTH_DIR": str(self.root / "auth"), "BAIDU_PCS_BIN": str(executable), "BDUSS": "must-not-inherit"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_stdin_only_argv_and_environment_isolation(self):
        result = await cli.run(17, "share-list", {"path": "/a ; $(echo bad)"}, credentials=CREDS)
        self.assertEqual(result["args"]["path"], "/a ; $(echo bad)")
        self.assertEqual(result["argv_count"], 1)
        self.assertIsNone(result["env_credential"])
        self.assertNotIn(SECRET, json.dumps(result))

    async def test_old_official_and_oauth_credentials_are_not_used(self):
        write_private(cli.user_dir(17) / "account.json", {"access_token": "old-token"})
        write_private(cli.user_dir(17) / "official/config.json", {"bduss": SECRET})
        with self.assertRaises(BaiduTokenExpired):
            await cli.run(17, "list")

    async def test_json_envelope_and_credential_safe_errors(self):
        for operation in ("invalid_json", "bad_schema", "error"):
            with self.subTest(operation=operation), self.assertRaises(BaiduDriveError) as raised:
                await cli.run(17, operation, credentials=CREDS)
            self.assertNotIn(SECRET, str(raised.exception))
        with self.assertRaises(BaiduTokenExpired):
            await cli.run(17, "expired", credentials=CREDS)

    async def test_output_limit_and_request_json_limit(self):
        with self.assertRaisesRegex(BaiduDriveError, "过大"):
            await cli.run(17, "overflow", credentials=CREDS)
        with self.assertRaisesRegex(BaiduDriveError, "过大"):
            await cli.run(17, "list", {"path": "a" * cli.MAX_INPUT}, credentials=CREDS)
        with self.assertRaisesRegex(BaiduDriveError, "格式"):
            await cli.run(17, "list", {"n": float("nan")}, credentials=CREDS)

    async def test_timeout_kills_child_process(self):
        pidfile = self.root / "pid"
        original, processes = cli.spawn, []
        async def capture(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(cli, "spawn", capture), self.assertRaisesRegex(BaiduDriveError, "超时"):
            await cli.run(17, "slow", {"pid": str(pidfile)}, credentials=CREDS, timeout=0.2)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(processes[0].pid, 0)

    async def test_final_disk_size_is_enforced_even_for_fast_process(self):
        directory = self.root / "download"
        directory.mkdir()
        with self.assertRaisesRegex(BaiduDriveError, "大小限制"):
            await cli.run(17, "disk", {"file": str(directory / "file")}, credentials=CREDS,
                          disk_directory=directory, disk_limit=512)

    def test_sensitive_query_keys_are_redacted(self):
        text = "https://passport.baidu.com/?BDUSS=secret&STOKEN=secret2&BDSTOKEN=secret3&temp_bduss=secret4&code=secret5"
        self.assertNotIn("secret", redact(text))


class PCSAuthorization(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"BAIDU_AUTH_DIR": str(self.root / "auth")})
        self.env.start()
        _locks.clear()
        auth._sessions.clear()
        auth._verified.clear()
        auth._registry_lock = asyncio.Lock()
        self.polls = asyncio.Queue()
        self.requests = []
        self.redirect = None
        self.omit_pan_stoken = False
        self.uid = "12345"
        self.fail_profile = False
        self.block_profile = None
        self.entered_profile = asyncio.Event()
        self.real_client = httpx.AsyncClient
        self.client_options = []
        self.clients = []

        async def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path == "/v2/api/getqrcode":
                return httpx.Response(200, json={"errno": 0, "sign": "server-only-sign", "imgurl": "passport.baidu.com/qr-test.png"})
            if path == "/qr-test.png":
                return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
            if path == "/channel/unicast":
                return httpx.Response(200, content=await self.polls.get())
            if path == "/v3/login/main/qrbdusslogin":
                return httpx.Response(302, headers=[
                    ("location", self.redirect or auth.PAN_URL),
                    ("set-cookie", "BDUSS=" + SECRET + "; Domain=.baidu.com; Path=/; Secure"),
                    ("set-cookie", "STOKEN=passport-token; Domain=passport.baidu.com; Path=/; Secure"),
                ])
            if path == "/disk/main":
                headers = {} if self.omit_pan_stoken else {
                    "set-cookie": "STOKEN=netdisk-test-token; Domain=pan.baidu.com; Path=/; Secure"}
                return httpx.Response(200, content=b"<html>netdisk</html>", headers=headers)
            raise AssertionError("unexpected URL: " + str(request.url))

        def client(**kwargs):
            self.client_options.append(kwargs)
            result = self.real_client(transport=httpx.MockTransport(handler), **kwargs)
            self.clients.append(result)
            return result

        async def run(user_id, operation, args=None, *, credentials=None, **kwargs):
            if operation == "profile":
                self.entered_profile.set()
                if self.block_profile:
                    await self.block_profile.wait()
                if self.fail_profile:
                    raise BaiduDriveError("safe network error")
                return {"uid": self.uid, "name": "账号 " + self.uid, "used_bytes": 10, "capacity_bytes": 100}
            if operation == "list":
                self.assertEqual(args["dir"], "/")
                return {"files": [], "has_more": False}
            raise AssertionError(operation)

        self.patches = [patch.object(auth.httpx, "AsyncClient", side_effect=client),
                        patch.object(cli, "binary", return_value=self.root / "bridge"),
                        patch.object(cli, "run", side_effect=run)]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await auth.shutdown()
        auth._sessions.clear()
        auth._verified.clear()
        _locks.clear()
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()
        self.temp.cleanup()

    async def confirm(self, session_id):
        await self.polls.put(json.dumps({"errno": 0, "channel_v": json.dumps({"status": 0, "v": "temporary-secret-value"})}).encode())
        await asyncio.wait_for(auth._sessions[session_id].task, 3)
        return await auth.session_status(17, session_id)

    async def login(self):
        start = await auth.start_authorization(17, True)
        self.assertEqual(start["status"], "waiting")
        result = await self.confirm(start["session_id"])
        self.assertEqual(result["status"], "authorized")
        return result

    async def test_real_cookie_domain_selection_private_commit_and_public_state(self):
        start = await auth.start_authorization(17, True)
        self.assertTrue(start["qrcode_image"].startswith("data:image/png;base64,"))
        self.assertNotIn("server-only-sign", json.dumps(start))
        self.assertTrue(self.client_options[0]["verify"])
        self.assertFalse(self.client_options[0]["follow_redirects"])
        await self.confirm(start["session_id"])
        status = await auth.get_status(17)
        self.assertTrue(status["connected"] and status["verified"])
        expected = hashlib.sha256(b"baidupcs:12345").hexdigest()[:32]
        self.assertEqual(status["account_id"], expected)
        self.assertNotIn("vip_type", status)
        self.assertEqual(status["used_bytes"], 10)
        for secret in (SECRET, "netdisk-test-token", "temporary-secret-value", "passport-token"):
            self.assertNotIn(secret, json.dumps(status))
        path = cli.config_path(17)
        credential = json.loads(path.read_text())
        self.assertEqual(credential["stoken"], "netdisk-test-token")
        self.assertNotIn("passport-token", credential["cookies"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(auth._sessions[start["session_id"]].sign, "")
        self.assertIsNone(auth._sessions[start["session_id"]].client)
        self.assertEqual(len(self.clients[0].cookies), 0)
        auth._verified.clear()
        self.assertTrue((await auth.get_status(17))["verified"])

    async def test_failed_switch_keeps_old_account_and_credentials(self):
        await self.login()
        previous = auth.account(17)
        before = cli.config_path(17).read_bytes()
        self.uid, self.fail_profile = "67890", True
        start = await auth.start_authorization(17, True)
        self.assertEqual((await self.confirm(start["session_id"]))["status"], "failed")
        self.assertEqual(auth.account(17), previous)
        self.assertEqual(cli.config_path(17).read_bytes(), before)

    async def test_stable_account_id_and_successful_switch_cleanup(self):
        await self.login()
        first = auth.account(17)["account_id"]
        old_path = cli.config_path(17)
        await self.login()
        self.assertEqual(auth.account(17)["account_id"], first)
        self.uid = "67890"
        await self.login()
        self.assertNotEqual(auth.account(17)["account_id"], first)
        self.assertFalse(old_path.exists())
        self.assertTrue(cli.config_path(17).exists())

    async def test_passport_service_token_alone_cannot_activate_netdisk(self):
        self.omit_pan_stoken = True
        start = await auth.start_authorization(17, True)
        self.assertEqual((await self.confirm(start["session_id"]))["status"], "failed")
        self.assertIsNone(auth.account(17))

    async def test_owner_binding_cancel_and_expiration(self):
        start = await auth.start_authorization(17, True)
        with self.assertRaises(BaiduDriveError):
            await auth.session_status(18, start["session_id"])
        with self.assertRaises(BaiduDriveError):
            await auth.cancel(18, start["session_id"])
        self.assertEqual((await auth.cancel(17, start["session_id"]))["status"], "cancelled")
        self.assertIsNone(auth.account(17))
        again = await auth.start_authorization(17, True)
        session = auth._sessions[again["session_id"]]
        session.expires_at = 1
        self.assertEqual((await auth.session_status(17, session.id))["status"], "expired")
        self.assertTrue(session.task.done())
        self.assertFalse(session.sign)
        self.assertIsNone(auth.account(17))

    async def test_cancel_during_validation_never_activates_new_account(self):
        await self.login()
        previous = auth.account(17)
        self.uid = "67890"
        self.block_profile = asyncio.Event()
        self.entered_profile.clear()
        start = await auth.start_authorization(17, True)
        await self.polls.put(b'{"errno":0,"channel_v":"{\\"status\\":0,\\"v\\":\\"temporary-secret-value\\"}"}')
        await asyncio.wait_for(self.entered_profile.wait(), 1)
        result = await asyncio.wait_for(auth.cancel(17, start["session_id"]), 1)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(auth.account(17), previous)
        self.assertEqual(len(list((cli.user_dir(17) / "pcs/connections").iterdir())), 1)

    async def test_redirects_cannot_leave_official_https_hosts(self):
        for url in ("https://attacker.test/", "http://pan.baidu.com/", "https://pan.baidu.com:444/", "https://pan.baidu.com@evil.test/"):
            self.redirect = url
            start = await auth.start_authorization(17, True)
            self.assertEqual((await self.confirm(start["session_id"]))["status"], "failed")
        self.assertTrue(all(r.url.scheme == "https" and r.url.host in {"passport.baidu.com", "pan.baidu.com"} for r in self.requests))
        self.assertIsNone(auth.account(17))

    async def test_network_failure_unverified_and_expiration_disconnected(self):
        await self.login()
        auth._verified.clear()
        self.fail_profile = True
        status = await auth.get_status(17)
        self.assertTrue(status["connected"])
        self.assertFalse(status["verified"])
        with patch.object(cli, "run", AsyncMock(side_effect=BaiduTokenExpired("登录失效"))):
            status = await auth.get_status(17)
        self.assertFalse(status["connected"])
        self.assertIsNotNone(auth.account(17))

    async def test_logout_cancels_pending_and_removes_only_pcs_credentials(self):
        await self.login()
        path = cli.config_path(17)
        write_private(cli.user_dir(17) / "official/config.json", {"legacy": True})
        start = await auth.start_authorization(17, True)
        self.assertFalse((await auth.logout(17))["connected"])
        self.assertFalse(path.exists())
        self.assertIsNone(auth.account(17))
        self.assertEqual((await auth.session_status(17, start["session_id"]))["status"], "cancelled")
        self.assertTrue((cli.user_dir(17) / "official/config.json").exists())

    async def test_single_owner_session_and_global_bound(self):
        first = await auth.start_authorization(17, True)
        second = await auth.start_authorization(17, True)
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(len(auth._sessions), 1)
        with patch.object(auth, "MAX_SESSIONS", 1):
            with self.assertRaisesRegex(BaiduDriveError, "过多"):
                await auth.start_authorization(18, True)

    def test_jsonp_strict_parsing_preserves_escaped_json(self):
        for value in (b'({"errno":0})', b'callback({"errno":0});', b'{"errno":0}'):
            self.assertEqual(auth._json(value), {"errno": 0})
        for value in (b'callback({"errno":0});evil()', b'<html>login</html>', b'[]'):
            with self.assertRaises(BaiduDriveError):
                auth._json(value)


class PCSActualTLS(unittest.IsolatedAsyncioTestCase):
    async def test_auth_client_rejects_local_self_signed_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, cert = root / "key.pem", root / "cert.pem"
            subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            async def handler(reader, writer):
                writer.close()
                await writer.wait_closed()
            loop = asyncio.get_running_loop()
            previous = loop.get_exception_handler()
            loop.set_exception_handler(lambda *_: None)  # Expected rejected TLS handshake only.
            try:
                server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
                async with server:
                    port = server.sockets[0].getsockname()[1]
                    async with auth._client() as client:
                        with self.assertRaisesRegex(httpx.ConnectError, "CERTIFICATE_VERIFY_FAILED"):
                            await client.get(f"https://127.0.0.1:{port}/")
                    await asyncio.sleep(0.01)  # Drain the expected failed TLS handshake.
            finally:
                loop.set_exception_handler(previous)


if __name__ == "__main__":
    unittest.main()
