"""Real isolated storage/HTTP handlers with simulated platform; no production data."""
import asyncio
from datetime import timedelta
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from test_mcp_gateway import Db

from app.api import api_router, deps
from app.mcp.bridge import BusinessBridge
from app.mcp.catalog import build_catalog
from app.api.routes.market_research import MarketSampleRequest
from common.models.market_observation import MarketMonitorState, MarketObservation, MarketSampleRun
from common.models.listing_monitor_task import ListingMonitorTask
from common.models.xy_account import XYAccount
from common.models.user import User, UserRole
from common.services import market_research as market
from common.services.market_metrics import exact_count, metric_change, price_number, want_from_tags
from common.services.xianyu_search_client import XianyuSearchClient, parse_search_item


def entry(item_id="123", tag="12人想要"):
    return {"data": {"item": {"main": {"exContent": {"itemId": item_id, "title": "自制进销存表格", "price": "3"},
        "clickParam": {"args": {"wantNum": "0", "serviceUtParams": [{"args": {"content": tag}}]}}}}}}


class MarketMetricTests(unittest.TestCase):
    def test_missing_is_unknown_and_zero_requires_explicit_counter(self):
        for value in (None, "", "NaN", True, -1, "2.5", "9999999999999"):
            self.assertIsNone(exact_count(value))
        self.assertEqual(exact_count("0"), 0)
        self.assertIsNone(parse_search_item(entry(tag="三天内上新"))["want_count"])
        self.assertEqual(parse_search_item(entry())["want_count"], "12")

    def test_rounded_labels_are_not_exact_or_truncated_suffixes(self):
        self.assertEqual(want_from_tags(["1.2万人想要"]), (12000, True))
        self.assertEqual(want_from_tags(["20+人想要"]), (20, True))
        parsed = parse_search_item(entry(tag="1.2万人想要"))
        self.assertEqual(parsed["want_count"], "12000")
        self.assertTrue(parsed["want_approximate"])

    def test_price_and_search_filters_reject_ambiguous_values(self):
        for value in ("3元起", "3-5", "NaN", "Infinity", -1):
            self.assertIsNone(price_number(value))
        self.assertEqual(float(price_number("¥0.10")), .1)
        with self.assertRaises(ValueError):
            MarketSampleRequest(keyword="test", account_id="1", sample_key="sample-001", price_min=9, price_max=2)
        with self.assertRaises(ValueError):
            MarketSampleRequest(keyword=" ", account_id="1", sample_key="sample-001")

    def test_changes_require_comparable_endpoints(self):
        first = {"want_count": 0, "want_source": "tag", "observed_at": "2026-01-01"}
        last = {"want_count": 10, "want_source": "tag", "observed_at": "2026-01-02"}
        def calc(**changes):
            return metric_change(first, {**last, **changes}, "want_count", "want_source", sample_count=2)
        self.assertEqual(calc()["value"], 10)
        self.assertEqual(calc(want_source="detail")["reason"], "source_changed")
        self.assertEqual(calc(want_count=None)["reason"], "missing_value")
        self.assertEqual(calc(want_approximate=True)["reason"], "rounded_counter")
        self.assertEqual(calc(observed_at=first["observed_at"])["reason"], "need_two_samples")
        self.assertEqual(metric_change({**first, "want_count": 20}, last, "want_count", "want_source", sample_count=2)["reason"], "counter_decreased")


class MarketStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        for model in (XYAccount, ListingMonitorTask, MarketSampleRun, MarketObservation, MarketMonitorState):
            model.__table__.create(self.engine)
        self.db = Db(self.engine)
        self.db.add(XYAccount(id=1, owner_id=1, account_id="mine", cookie="test-only", login_method="cookie", status="active"))
        self.db.add(XYAccount(id=2, owner_id=2, account_id="other", cookie="test-only", login_method="cookie", status="active"))
        await self.db.commit()
        self.request = dict(keyword="表格", account_id="mine", sample_key="sample-0001", start_page=1,
                            pages=1, page_size=20, detail_limit=0, price_min=None, price_max=None)
        self.svc = market.MarketResearchService(self.db, 1)

    async def asyncTearDown(self):
        self.db.db.close()
        self.engine.dispose()

    async def snapshot(self, *, owner=1, values=(10,), age=1, source="search.serviceUtParams", status="success", filters=None):
        run = market.new_sample(owner, "表格", "test", filters or {})
        run.started_at -= timedelta(days=age)
        self.db.add(run)
        await self.db.flush()
        items = [{"item_id": str(100 + i), "title": "自制 Excel 表格", "price": "3", "want_count": v,
                  "want_source": source, "browse_count": None} for i, v in enumerate(values)]
        await market.finish_sample(self.db, run, items, status=status, pages_completed=1)
        return run

    async def test_history_scope_and_owner_boundaries(self):
        older = await self.snapshot(values=(10, None), age=2)
        await self.snapshot(values=(15, 0), age=1)
        await self.snapshot(owner=2, values=(900,), age=0)
        await self.snapshot(values=(800,), age=0, filters={"different": True})
        result = await self.svc.candidates(older.scope_key)
        self.assertEqual(result["summary"]["sample_items"], 2)
        self.assertEqual(result["list"][0]["changes"]["want_count"]["value"], 5)
        self.assertEqual(result["list"][1]["changes"]["want_count"]["reason"], "missing_value")
        self.assertEqual((await self.svc.history("100", scope=older.scope_key))["total"], 2)
        foreign = market.MarketResearchService(self.db, 99)
        self.assertEqual((await foreign.candidates(older.scope_key))["list"], [])

    async def test_failed_and_partial_samples_do_not_erase_history(self):
        old = await self.snapshot(age=3)
        partial = await self.snapshot(values=(13,), age=2, status="partial")
        await self.snapshot(age=1, status="failed")
        result = await self.svc.candidates(old.scope_key)
        self.assertEqual(result["run"]["id"], partial.id)
        self.assertEqual(result["latest_attempt"]["status"], "failed")
        self.assertEqual(result["list"][0]["sample_count"], 2)

    async def test_changed_source_and_decrease_do_not_generate_growth(self):
        a = await self.snapshot(values=(20,), age=3)
        await self.snapshot(values=(30,), age=2, source="detail.wantCnt")
        await self.snapshot(values=(25,), age=1)
        result = await self.svc.candidates(a.scope_key)
        self.assertIsNone(result["list"][0]["changes"]["want_count"]["value"])

    async def test_single_sample_and_rules_are_not_ai_recommendation(self):
        run = await self.snapshot()
        result = await self.svc.candidates(run.scope_key, exclude_terms=["Excel"])
        row = result["list"][0]
        self.assertEqual(row["candidate_signal"], "filtered_out")
        self.assertEqual(row["changes"]["want_count"]["reason"], "need_two_samples")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(row["analysis_status"], "agent_review_required")
        self.assertFalse(market.keyword_matches("q1", "q1r5"))

    async def test_collection_replays_and_cannot_borrow_other_account(self):
        with patch.object(market.XianyuSearchClient, "search", AsyncMock(return_value={"success": True, "items": [entry(), entry()], "has_next_page": False})) as search:
            result = await market.collect_sample(self.db, 1, self.request)
            replay = await market.collect_sample(self.db, 1, self.request)
            self.assertEqual(result["run"]["item_count"], 1)
            self.assertTrue(replay["replayed"])
            search.assert_awaited_once()
            with self.assertRaises(ValueError):
                await market.collect_sample(self.db, 1, {**self.request, "keyword": "改参数"})
            with self.assertRaises(ValueError):
                await market.collect_sample(self.db, 1, {**self.request, "sample_key": "sample-0002", "account_id": "other"})

    async def test_partial_pages_and_detail_sources_remain_explicit(self):
        request = {**self.request, "pages": 2, "detail_limit": 1}
        with patch.object(market.XianyuSearchClient, "search", AsyncMock(side_effect=[
            {"success": True, "items": [entry()], "has_next_page": True},
            {"success": False, "error": "network timeout", "items": []}])), \
             patch.object(market.XianyuItemDetailClient, "get_detail", AsyncMock(return_value={
                 "success": True, "detail": {"itemDO": {"wantCnt": 16, "browseCnt": 42}}})), \
             patch.object(market.asyncio, "sleep", AsyncMock()):
            result = await market.collect_sample(self.db, 1, request)
        self.assertEqual(result["run"]["status"], "partial")
        self.assertEqual(result["run"]["pages_completed"], 1)
        history = await self.svc.history("123")
        self.assertEqual(history["list"][0]["want_source"], "detail.wantCnt")
        self.assertEqual(history["list"][0]["browse_count"], 42)

    async def test_no_network_response_is_not_zero_demand(self):
        with patch.object(market.XianyuSearchClient, "search", AsyncMock(side_effect=TimeoutError())):
            result = await market.collect_sample(self.db, 1, self.request)
        self.assertEqual(result["run"]["status"], "failed")
        self.assertEqual((await self.svc.history("123"))["total"], 0)

    async def test_login_failure_does_not_trigger_detail_requests(self):
        with patch.object(market.XianyuSearchClient, "search", AsyncMock(side_effect=[
            {"success": True, "items": [entry()], "has_next_page": True},
            {"success": False, "error": "Session过期"}])), \
             patch.object(market.XianyuItemDetailClient, "get_detail", AsyncMock()) as detail, \
             patch.object(market.asyncio, "sleep", AsyncMock()):
            result = await market.collect_sample(self.db, 1, {**self.request, "pages": 2, "detail_limit": 1})
        self.assertEqual(result["run"]["error_code"], "login_required")
        detail.assert_not_awaited()

    async def test_bad_search_structure_is_failure(self):
        with patch("common.services.xianyu_search_client.mtop_call", AsyncMock(return_value={"success": True, "res": {"data": {}}})):
            result = await XianyuSearchClient("mine", "unused").search("表格")
        self.assertFalse(result["success"])

    async def test_provider_over_return_is_bounded_to_requested_page_size(self):
        with patch.object(market.XianyuSearchClient, "search", AsyncMock(return_value={
            "success": True, "items": [entry(str(1000+i)) for i in range(30)], "has_next_page": False})):
            result = await market.collect_sample(self.db, 1, {**self.request, "page_size": 5})
        self.assertEqual(result["run"]["item_count"], 5)

    async def test_history_cap_does_not_pause_successful_collection(self):
        task = ListingMonitorTask(id=1, owner_id=1, keyword="表格", is_enabled=True)
        self.db.add(task); await self.db.commit()
        run = await self.snapshot(values=tuple(range(301)))
        self.assertEqual(run.status, "partial")
        self.assertEqual(run.item_count, 300)
        await market.update_monitor_state(self.db, task, run, collection_status="success")
        self.assertEqual((await self.db.get(MarketMonitorState, 1)).consecutive_failures, 0)

    async def test_retention_only_deletes_expired_research_rows(self):
        old = await self.snapshot(age=91)
        await self.snapshot(age=1)
        deleted = await market.cleanup_market_history(self.db)
        self.assertEqual(deleted, {"observations": 1, "runs": 1})
        self.assertIsNone(await self.db.get(MarketSampleRun, old.id))
        self.assertIsNotNone(await self.db.get(XYAccount, 1))

    async def test_pause_persists_and_success_recovers_without_enabling_task(self):
        task = ListingMonitorTask(id=1, owner_id=1, keyword="表格", is_enabled=False)
        self.db.add(task); await self.db.commit()
        for _ in range(3):
            run = await self.snapshot(status="failed")
            run.error_code = "login_required"
            await market.update_monitor_state(self.db, task, run)
        state = await self.db.get(MarketMonitorState, 1)
        self.assertEqual(state.status, "paused")
        self.assertGreater(state.paused_until, market.get_beijing_now_naive())
        async with Db(self.engine) as other_session:
            self.assertEqual((await other_session.get(MarketMonitorState, 1)).consecutive_failures, 3)
        ok = await self.snapshot()
        await market.update_monitor_state(self.db, task, ok)
        self.assertEqual(state.status, "ready")
        self.assertFalse(task.is_enabled)

    async def test_candidates_have_bounded_queries_not_per_item_queries(self):
        for age in (3, 2, 1):
            run = await self.snapshot(values=tuple(range(100)), age=age)
        calls = []
        def count(*args): calls.append(1)
        event.listen(self.engine, "before_cursor_execute", count)
        try:
            result = await self.svc.candidates(run.scope_key, limit=100)
        finally:
            event.remove(self.engine, "before_cursor_execute", count)
        self.assertEqual(len(result["list"]), 100)
        self.assertLessEqual(len(calls), 6)

    async def test_mcp_uses_real_handlers_with_owner_scope_and_bounded_schema(self):
        await self.snapshot(owner=2)
        app = FastAPI(); app.include_router(api_router, prefix="/api/v1")
        catalog = build_catalog(app.routes)
        names = [name for name in catalog if name.startswith("market_")]
        self.assertEqual(len(names), 5)
        self.assertTrue(all(catalog[n].scope == "xianyu:read" for n in names))
        bridge = BusinessBridge(catalog)
        async def db_override(): yield self.db
        bridge.app.dependency_overrides[deps.get_db_session] = db_override
        try:
            result = await bridge.invoke(catalog["market_get_market_history"], {"path": {"item_id": "100"}}, User(id=1, role=UserRole.ADMIN))
            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["total"], 0)
            invalid = await bridge.invoke(catalog["market_list_market_samples"], {"query": {"page_size": 5000}}, User(id=1))
            self.assertFalse(invalid["success"])
        finally:
            await bridge.close()

    async def test_scheduler_pauses_without_platform_calls_then_records_recovery(self):
        path = Path(__file__).resolve().parents[2] / "scheduler/app/services/scheduler/listing_monitor_task.py"
        spec = importlib.util.spec_from_file_location("market_listing_monitor", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        task = ListingMonitorTask(id=1, owner_id=1, keyword="表格", is_enabled=True, collect_pages=1)
        self.db.add(task)
        self.db.add(MarketMonitorState(task_id=1, owner_id=1, consecutive_failures=3,
                     status="paused", paused_until=market.get_beijing_now_naive() + timedelta(minutes=10)))
        await self.db.commit()
        service = module.ListingMonitorTaskService()
        service._collect_task = AsyncMock(return_value={"status": "success", "items": [parse_search_item(entry())], "pages": 1, "error_code": None})
        with patch.object(module, "async_session_maker", lambda: Db(self.engine)):
            self.assertEqual((await service._process_task(task))["status"], "paused")
            service._collect_task.assert_not_awaited()
            state = await self.db.get(MarketMonitorState, 1)
            state.paused_until = market.get_beijing_now_naive() - timedelta(seconds=1)
            await self.db.commit()
            self.assertEqual((await service._process_task(task))["status"], "success")
        self.db.db.expire_all()
        self.assertEqual((await self.db.get(MarketMonitorState, 1)).status, "ready")
        self.assertEqual((await self.svc.history("123"))["total"], 1)
        current_task = await self.db.get(ListingMonitorTask, 1)
        current_task.is_enabled = False
        await self.db.commit()
        service._collect_task.reset_mock()
        with patch.object(module, "async_session_maker", lambda: Db(self.engine)):
            self.assertEqual((await service._process_task(task))["status"], "disabled")
        service._collect_task.assert_not_awaited()


class MarketProtocolTests(unittest.IsolatedAsyncioTestCase):
    from test_mcp_gateway import McpProtocolTests as _Fixtures
    client = _Fixtures.client
    asyncTearDown = _Fixtures.asyncTearDown

    async def asyncSetUp(self):
        await self._Fixtures.asyncSetUp(self)
        for model in (MarketSampleRun, MarketObservation, MarketMonitorState):
            model.__table__.create(self.engine)

    async def test_actual_sdk_exposes_five_owned_read_tools(self):
        async with self.client(self.read) as client:
            self.assertFalse(any(t.name.startswith("market_") for t in (await client.list_tools()).tools))
        async with self.client() as client:
            names = {t.name for t in (await client.list_tools()).tools if t.name.startswith("market_")}
            self.assertEqual(len(names), 5)
            result = await client.call_tool("market_list_market_samples", {})
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["data"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
