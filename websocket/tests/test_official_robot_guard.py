"""Only fixture callbacks; no platform messages, credentials or network."""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'websocket'), str(ROOT)]
from app.services.xianyu.message_handler import MessageHandler
from common.utils.xianyu_official_accounts import ensure_xianyu_message_recipient, message_targets_official_robot


class RobotGuardTests(unittest.IsolatedAsyncioTestCase):
    def handler(self):
        with patch.object(MessageHandler, '_load_message_expire_time', return_value=3600):
            h = MessageHandler('fixture-shop')
        h.extract_message_id = lambda _: None
        h.is_system_tip_message = lambda _: False
        h._on_chat_message = AsyncMock()
        h._on_card_update_message = AsyncMock()
        return h

    async def test_robot_chat_never_reaches_auto_reply(self):
        h = self.handler()
        h.is_chat_message = lambda _: True
        h.parse_chat_message = lambda _: {'send_user_id': '2223046249624@goofish', 'send_message': 'fixture'}
        self.assertTrue(await h._process_single_message({}, None))
        h._on_chat_message.assert_not_awaited()

    async def test_regular_buyer_chat_is_dispatched(self):
        h = self.handler()
        h.is_chat_message = lambda _: True
        h.parse_chat_message = lambda _: {'send_user_id': 'fixture-buyer', 'send_message': '请问怎么安装'}
        self.assertTrue(await h._process_single_message({}, None))
        h._on_chat_message.assert_awaited_once()

    async def test_buyer_payment_card_keeps_delivery_callback(self):
        h = self.handler()
        message = {'1': 'fixture-card', '2': 'fixture-chat@goofish', '4': {
            'senderUserId': 'fixture-buyer', 'reminderContent': '买家已付款',
            'reminderUrl': 'https://example.invalid/?itemId=fixture-item'}}
        self.assertTrue(await h._process_single_message(message, None))
        h._on_card_update_message.assert_awaited_once()
        self.assertEqual(h._on_card_update_message.await_args.args[0]['send_user_id'], 'fixture-buyer')

    async def test_robot_card_does_not_trigger_delivery(self):
        h = self.handler()
        message = {'1': 'fixture-card', '4': {'senderUserId': '2223046249624', 'reminderContent': 'fixture'}}
        self.assertTrue(await h._process_single_message(message, None))
        h._on_card_update_message.assert_not_awaited()

    def test_send_guard_handles_suffix_and_mixed_receivers(self):
        with self.assertRaises(ValueError):
            ensure_xianyu_message_recipient(' 2223046249624@goofish ')
        ensure_xianyu_message_recipient('fixture-buyer')
        self.assertTrue(message_targets_official_robot({'body': [{}, {'actualReceivers': ['fixture', '2223046249624@goofish']}]}))
        self.assertFalse(message_targets_official_robot({'body': [{'actualReceivers': ['fixture-buyer']}]}))
