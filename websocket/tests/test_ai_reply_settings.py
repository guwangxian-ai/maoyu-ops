"""AI sales limits must retain an explicitly configured zero."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "websocket"))

from app.services.xianyu.ai_reply_engine import AIReplyEngine


class AIReplySettingsTests(unittest.TestCase):
    def setUp(self):
        self.engine = AIReplyEngine()

    def test_zero_limits_remain_zero(self):
        settings = self.engine._extract_ai_settings({
            "max_bargain_rounds": 0,
            "max_discount_percent": 0,
            "max_discount_amount": 0,
        })
        for key in ("max_bargain_rounds", "max_discount_percent", "max_discount_amount"):
            with self.subTest(key=key):
                self.assertEqual(settings[key], 0)

    def test_missing_or_null_limits_keep_defaults(self):
        for stored in ({}, {"max_bargain_rounds": None, "max_discount_percent": None, "max_discount_amount": None}):
            settings = self.engine._extract_ai_settings(stored)
            with self.subTest(stored=stored):
                self.assertEqual(settings["max_bargain_rounds"], 3)
                self.assertEqual(settings["max_discount_percent"], 10)
                self.assertEqual(settings["max_discount_amount"], 100)

    def test_independent_limits_and_legacy_strings(self):
        settings = self.engine._extract_ai_settings({
            "max_bargain_rounds": "2",
            "max_discount_percent": "5",
            "max_discount_amount": "0",
        })
        self.assertEqual(settings["max_bargain_rounds"], 2)
        self.assertEqual(settings["max_discount_percent"], 5)
        self.assertEqual(settings["max_discount_amount"], 0)


if __name__ == "__main__":
    unittest.main()
