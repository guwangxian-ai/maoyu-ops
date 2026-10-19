"""Price-limit replies must respect item-specific fulfillment rules."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "websocket"))

from app.services.xianyu.ai_reply_engine import AIReplyEngine


class AIPriceLimitReplyTests(unittest.IsolatedAsyncioTestCase):
    async def generate(self, prompts, item_id="free-item", rounds=3):
        engine = AIReplyEngine()
        engine.is_ai_enabled = AsyncMock(return_value=True)
        engine.get_ai_settings = AsyncMock(return_value={
            "ai_enabled": True,
            "max_bargain_rounds": 3,
            "custom_prompts": prompts,
            "provider_type": "openai_compatible",
            "api_key": "test-only-placeholder",
        })
        engine._call_openai_api = AsyncMock(return_value="model reply")
        engine._save_ai_message_with_retry = AsyncMock(return_value=True)
        session = MagicMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(side_effect=[
            MagicMock(all=lambda: []),
            MagicMock(all=lambda: []),
            MagicMock(scalar=lambda: rounds),
        ])
        reply = await engine.generate_reply(
            "多少钱", {"title": "Example", "price": "0.01"},
            "test-chat", "test-account", "test-user", item_id,
            session, skip_wait=True,
        )
        return reply, session, engine

    async def test_configured_item_uses_its_reply_after_limit(self):
        text = "这份资料免费领取，无需付款。"
        reply, session, engine = await self.generate(json.dumps({
            "price_limit_replies": {"free-item": text},
        }))
        self.assertEqual(reply, text)
        self.assertEqual(session.add.call_args.args[0].content, text)
        engine._call_openai_api.assert_not_awaited()

    async def test_other_item_keeps_existing_limit(self):
        reply, _, engine = await self.generate({
            "price_limit_replies": {"free-item": "免费领取"},
        }, item_id="paid-item")
        self.assertIn("不能再便宜", reply)
        engine._call_openai_api.assert_not_awaited()

    async def test_missing_or_malformed_override_keeps_existing_limit(self):
        for prompts in ("", "not-json", "[]", {"price_limit_replies": []},
                        {"price_limit_replies": {"free-item": " "}},
                        {"price_limit_replies": {"free-item": 123}}):
            with self.subTest(prompts=prompts):
                reply, _, _ = await self.generate(prompts)
                self.assertIn("不能再便宜", reply)

    async def test_before_limit_still_uses_the_configured_model_prompt(self):
        reply, _, engine = await self.generate({
            "price": "Answer with the item's claim conditions.",
            "price_limit_replies": {"free-item": "免费领取"},
        }, rounds=1)
        self.assertEqual(reply, "model reply")
        messages = engine._call_openai_api.call_args.args[1]
        self.assertEqual(messages[0]["content"], "Answer with the item's claim conditions.")


if __name__ == "__main__":
    unittest.main()
