"""Search preserves errors, caps output, and leaves verification to the user."""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend-web")]
from app.services.search import searcher


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_failure_is_not_retried_or_replaced(self):
        error = {"items": [], "total": 0, "error": "浏览器未安装"}
        with patch.object(searcher.ItemSearchService, "search_items", AsyncMock(return_value=error)) as search:
            self.assertEqual(await searcher.search_xianyu_items("query", user_id="1"), error)
            search.assert_awaited_once()

    async def prepared(self, captcha=False):
        service = searcher.ItemSearchService(user_id="1")
        service.browser = AsyncMock()
        service.get_first_valid_cookie = AsyncMock(return_value={"id": "own", "value": "test"})
        service._find_search_input = AsyncMock(return_value=AsyncMock())
        service.slider_handler.detect_slider = AsyncMock(return_value=(captcha, "test"))
        service.slider_handler.handle_verification = AsyncMock()
        async def click(*_):
            service.data_list.extend([{"item_id": str(i), "want_count": i} for i in range(30)])
        service.browser.click.side_effect = click
        # Registering an event callback is synchronous, not a coroutine.
        service.browser.on_response = lambda cb: None
        return service

    async def test_page_size_is_respected(self):
        service = await self.prepared()
        with patch.object(searcher.asyncio, "sleep", AsyncMock()):
            result = await service.search_items("query", page_size=10)
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(result["total"], 30)
        self.assertEqual(result["items"][0]["item_id"], "29")
        service.browser.close_browser.assert_awaited_once()

    async def test_verification_stops_without_attempting_to_solve(self):
        service = await self.prepared(captcha=True)
        with patch.object(searcher.asyncio, "sleep", AsyncMock()):
            result = await service.search_items("query")
        self.assertEqual(result["error_code"], "verification_required")
        self.assertEqual(result["items"], [])
        service.slider_handler.handle_verification.assert_not_awaited()
        service.browser.close_browser.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
