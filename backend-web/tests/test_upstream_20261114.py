"""注册、通知与公共发布回归。只在云端隔离容器运行，不调用真实平台或邮件。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select

from test_mcp_gateway import Db
from app.api import deps
from app.api.routes import auth, captcha, selectable_items
from app.mcp.catalog import build_catalog
from app.services.notification_service import MessageNotificationService
from app.services.system_setting_service import SystemSettingService
from common.models.message_notification import MessageNotification
from common.models.notification_channel import NotificationChannel
from common.models.system_setting import SystemSetting
from common.models.xy_account import XYAccount
from common.schemas.notification import MessageNotificationSet
from common.services.email_verification_code_service import check_email_code, email_code_store
from common.services.registration_policy import is_registration_enabled
from common.utils.notification_types import allows_notification


EMAIL = 'isolated@example.com'


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        email_code_store.clear()
        self.settings = {'registration_enabled': 'true'}
        self.setting_service = SimpleNamespace(list_settings=AsyncMock(side_effect=lambda: self.settings.copy()))
        self.users = SimpleNamespace(get_by_username=AsyncMock(return_value=None),
                                     get_by_email=AsyncMock(return_value=None), create=AsyncMock())
        self.app = FastAPI()
        self.app.include_router(auth.router, prefix='/api/v1/auth')
        self.app.include_router(captcha.router, prefix='/api/v1')
        self.app.dependency_overrides[deps.get_system_setting_service] = lambda: self.setting_service
        self.app.dependency_overrides[deps.get_user_service] = lambda: self.users
        self.app.dependency_overrides[deps.get_db_session] = lambda: None
        self.patches = [
            patch.object(captcha, 'SystemSettingService', return_value=self.setting_service),
            patch('app.services.user_service.UserService', return_value=self.users),
            patch.object(captcha, 'record_email_log', new=AsyncMock()),
            patch('app.services.email_service.send_verification_code_email', new=AsyncMock(return_value=(True, 'ok'))),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False), base_url='http://isolated')

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches): p.stop()
        email_code_store.clear()

    def code(self, kind='register'):
        import time
        record = {'code': '123456', 'type': kind, 'expires_at': time.time() + 300}
        email_code_store[EMAIL] = record
        return record

    async def register(self):
        return await self.client.post('/api/v1/auth/register', json={
            'username': 'isolated', 'email': EMAIL, 'password': 'fixture-password', 'verification_code': '123456'})

    async def send(self, kind='register'):
        return await self.client.post('/api/v1/captcha/send-email-code', json={'email': EMAIL, 'type': kind})

    async def test_registration_boolean_compatibility(self):
        for value in [True, 1, 'true', ' TRUE ', '1', 'yes', ' ON ']: self.assertTrue(is_registration_enabled(value))
        for value in [False, 0, None, '', 'false', 'off', 'no', 'garbage']: self.assertFalse(is_registration_enabled(value))

    async def test_disabled_register_does_not_consume_code_or_create_user(self):
        self.settings.clear()
        original = self.code()
        response = await self.register()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['code'], 403)
        self.users.create.assert_not_awaited()
        self.assertIs(email_code_store[EMAIL], original)

    async def test_enabled_register_consumes_code_and_creates_once(self):
        self.code()
        self.assertTrue((await self.register()).json()['success'])
        self.assertNotIn(EMAIL, email_code_store)
        self.users.create.assert_awaited_once()
        self.assertEqual((await self.register()).status_code, 400)
        self.users.create.assert_awaited_once()

    async def test_disabled_registration_blocks_sending_and_verifying(self):
        self.settings['registration_enabled'] = 'false'
        self.code()
        self.assertEqual((await self.send()).json()['code'], 403)
        response = await self.client.post('/api/v1/captcha/verify-email-code', params={'email': EMAIL, 'code': '123456'})
        self.assertEqual(response.json()['code'], 403)
        self.mocks[3].assert_not_awaited()
        self.assertIn(EMAIL, email_code_store)

    async def test_settings_error_cannot_create_user(self):
        self.setting_service.list_settings.side_effect = RuntimeError('isolated read failure')
        self.code()
        response = await self.register()
        self.assertEqual(response.status_code, 500)
        self.users.create.assert_not_awaited()
        self.assertIn(EMAIL, email_code_store)

    async def test_login_code_remains_available_when_registration_closed(self):
        self.settings['registration_enabled'] = 'false'
        self.code('login')
        response = await self.client.post('/api/v1/captcha/verify-email-code', params={'email': EMAIL, 'code': '123456', 'code_type': 'login'})
        self.assertTrue(response.json()['success'])

    async def test_code_type_and_attempt_limit_apply_to_public_verification(self):
        self.code('login')
        self.assertFalse(check_email_code(EMAIL, '123456', 'register')[0])
        for _ in range(5):
            response = await self.client.post('/api/v1/captcha/verify-email-code', params={'email': EMAIL, 'code': 'wrong', 'code_type': 'login'})
            self.assertFalse(response.json()['success'])
        self.assertNotIn(EMAIL, email_code_store)

    async def test_pending_email_blocks_verify_and_concurrent_send(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def sending(*args, **kwargs):
            started.set()
            await release.wait()
            return True, 'ok'
        self.mocks[3].side_effect = sending
        task = asyncio.create_task(self.send())
        await asyncio.wait_for(started.wait(), 2)
        record = email_code_store[EMAIL]
        self.assertFalse(check_email_code(EMAIL, record['code'], 'register')[0])
        self.assertFalse((await self.send()).json()['success'])
        release.set()
        self.assertTrue((await task).json()['success'])
        self.assertFalse(record['is_pending'])
        self.mocks[3].assert_awaited_once()

    async def test_mail_failure_and_exception_clear_only_current_record(self):
        self.mocks[3].return_value = False, 'fixture failed'
        self.assertFalse((await self.send()).json()['success'])
        self.assertNotIn(EMAIL, email_code_store)
        later = self.code()
        email_code_store.clear()
        async def replace_then_fail(*args, **kwargs):
            email_code_store[EMAIL] = later
            raise RuntimeError('fixture failed')
        self.mocks[3].side_effect = replace_then_fail
        self.assertFalse((await self.send()).json()['success'])
        self.assertIs(email_code_store[EMAIL], later)

    async def test_cancelled_send_removes_pending_code(self):
        started = asyncio.Event()
        async def sending(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        self.mocks[3].side_effect = sending
        task = asyncio.create_task(self.send())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertNotIn(EMAIL, email_code_store)


class NestedDb(Db):
    @asynccontextmanager
    async def begin_nested(self):
        with self.db.begin_nested(): yield


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine('sqlite://')
        for model in [SystemSetting, XYAccount, NotificationChannel, MessageNotification]: model.__table__.create(self.engine)
        self.db = NestedDb(self.engine)

    async def asyncTearDown(self):
        self.db.db.close()
        self.engine.dispose()

    async def test_default_settings_do_not_overwrite_existing_registration_value(self):
        self.db.add(SystemSetting(key='registration_enabled', value=' YES '))
        await self.db.commit()
        service = SystemSettingService(self.db)
        self.assertEqual((await service.list_settings())['registration_enabled'], 'true')
        row = (await self.db.execute(select(SystemSetting).where(SystemSetting.key == 'registration_enabled'))).scalar_one()
        self.assertEqual(row.value, ' YES ')
        await service.set_setting('registration_enabled', 'off')
        self.assertEqual((await service.list_settings())['registration_enabled'], 'false')

    async def test_missing_registration_setting_is_created_closed(self):
        self.assertEqual((await SystemSettingService(self.db).list_settings())['registration_enabled'], 'false')

    async def test_notification_subscription_roundtrip_and_old_client_toggle(self):
        self.db.add(XYAccount(id=1, owner_id=1, account_id='fixture-account', cookie='fixture', login_method='qr'))
        self.db.add(NotificationChannel(id=1, owner_id=1, name='fixture', channel_type='bark', enabled=True))
        self.db.add(NotificationChannel(id=2, owner_id=2, name='foreign', channel_type='bark', enabled=True))
        await self.db.commit()
        service = MessageNotificationService(self.db)
        self.assertTrue(await service.set_notification(1, 'fixture-account', MessageNotificationSet(channel_id=1, notify_types=['account'])))
        self.assertTrue(await service.set_notification(1, 'fixture-account', MessageNotificationSet(channel_id=1, enabled=False)))
        row = (await self.db.execute(select(MessageNotification))).scalar_one()
        self.assertEqual(row.notify_types, 'account')
        self.assertFalse(row.enabled)
        listed = await service.list_for_account(1, 'fixture-account')
        self.assertEqual(listed[0]['notify_types'], ['account'])
        self.assertFalse(await service.set_notification(1, 'fixture-account', MessageNotificationSet(channel_id=2)))
        self.assertFalse(await service.set_notification(2, 'fixture-account', MessageNotificationSet(channel_id=1)))

    async def test_notification_event_intersection_and_legacy_defaults(self):
        self.assertTrue(allows_notification(None, None, 'chat'))
        self.assertTrue(allows_notification('account,delivery', {'notify_types': ['account']}, 'account'))
        self.assertFalse(allows_notification('account,delivery', {'notify_types': ['account']}, 'delivery'))
        self.assertFalse(allows_notification('account', {}, 'chat'))
        self.assertTrue(allows_notification('account', {}, None))
        self.assertFalse(allows_notification(None, {}, 'unknown'))


class PublishAndMcpTests(unittest.TestCase):
    def test_quantity_keeps_zero_and_maximum_inventory(self):
        from common.services.xianyu_item_payload_builder import _resolve_quantity
        self.assertEqual(_resolve_quantity({'quantity': 0}), 0)
        self.assertEqual(_resolve_quantity({'quantity': 999999}), 999999)
        self.assertEqual(_resolve_quantity({'quantity': 1000000}), 999999)

    def test_shared_publisher_loads_without_backend_app_package(self):
        root = Path(__file__).resolve().parents[2]
        code = '''import app, json, sys
from common.services import xianyu_publish_service as p
before=list(app.__path__)
classes=[p.get_xianyu_direct_publisher_class(),p.get_xianyu_personal_publisher_class(),p.get_publish_account_capability_service_class()]
assert all(c.__module__.startswith('common.services.') for c in classes)
assert list(app.__path__)==before
assert not any('_shared_xianyu' in n for n in sys.modules)
print('shared publishers isolated')'''
        env = {**os.environ, 'PYTHONPATH': str(root / 'websocket') + os.pathsep + str(root)}
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_compatibility_exports_refer_to_same_classes(self):
        from app.services.xianyu_direct_publisher import XianyuDirectPublisher as old
        from common.services.xianyu_direct_publisher import XianyuDirectPublisher as shared
        self.assertIs(old, shared)
        from common.services.xianyu_publish_service import ensure_publish_capability_reliable
        self.assertFalse(ensure_publish_capability_reliable({'success': True, 'is_fish_shop': False, 'detection_reliable': False})['success'])

    def test_selectable_mcp_is_readonly_bounded_and_requires_login(self):
        app = FastAPI()
        app.include_router(selectable_items.router, prefix='/api/v1')
        spec = build_catalog(app.routes)['items_get_selectable_items_paginated']
        self.assertEqual(spec.scope, 'xianyu:read')
        self.assertTrue(spec.readonly)
        self.assertFalse(spec.allowed([]))
        self.assertEqual(spec.schema['properties']['query']['properties']['page_size']['maximum'], 100)


class AmapTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_is_bounded_and_closes_session(self):
        import aiohttp
        from common.services import amap_inputtips_transport as transport
        class Client:
            calls = 0
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
            def get(self, *args, **kwargs):
                self.calls += 1
                raise aiohttp.ClientError('fixture')
        client = Client()
        with patch.object(transport.aiohttp, 'ClientSession', return_value=client), patch.object(transport.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(aiohttp.ClientError):
                await transport.request_inputtips('https://isolated', params={'key':'fixture'}, headers={})
        self.assertEqual(client.calls, 3)
        self.assertTrue(client.closed)

    async def test_cancelled_request_closes_response_and_session(self):
        from common.services import amap_inputtips_transport as transport
        class Response:
            status = 200
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
            async def text(self): raise asyncio.CancelledError()
        response = Response()
        class Client:
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
            def get(self, *args, **kwargs): return response
        client = Client()
        with patch.object(transport.aiohttp, 'ClientSession', return_value=client):
            with self.assertRaises(asyncio.CancelledError):
                await transport.request_inputtips('https://isolated', params={}, headers={})
        self.assertTrue(response.closed and client.closed)
