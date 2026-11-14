"""Run only in a disposable, network-isolated MySQL container; never production."""
import asyncio
from datetime import timedelta
import json
import os
import statistics
import time

from sqlalchemy import insert, select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from common.db.market_schema import ensure_market_schema
from common.models.market_observation import MarketSampleRun, MarketObservation
from common.services.market_research import MarketResearchService, get_beijing_now_naive, scope_key


async def main():
    assert os.environ.get("MARKET_ISOLATED_TEST") == "1"
    database = os.environ["MYSQL_DATABASE"]
    assert database == "market_isolated_test" and os.environ["MYSQL_HOST"] == "127.0.0.1"
    engine = create_async_engine("mysql+asyncmy://root:" + os.environ["MYSQL_PASSWORD"] + "@127.0.0.1/" + database)
    try:
        async with engine.begin() as conn:
            await ensure_market_schema(conn)
            await ensure_market_schema(conn)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        scope = scope_key("isolated-fixture", "test", {})
        now = get_beijing_now_naive()
        async with factory() as session:
            for index in range(100):
                run_id = f"{index:032d}"
                timestamp = now - timedelta(minutes=100-index)
                await session.execute(insert(MarketSampleRun), [{"id": run_id, "owner_id": 1,
                    "sample_key": run_id, "scope_key": scope, "keyword": "isolated-fixture", "source": "test",
                    "filters": {}, "status": "success", "pages_requested": 1, "pages_completed": 1,
                    "item_count": 100, "started_at": timestamp, "finished_at": timestamp}])
                await session.execute(insert(MarketObservation), [{"run_id": run_id, "owner_id": 1,
                    "scope_key": scope, "item_id": str(1000+item), "title": "isolated fixture",
                    "price": 3, "price_source": "search.list_price", "want_count": index,
                    "want_source": "search.serviceUtParams", "want_approximate": False,
                    "observed_at": timestamp} for item in range(100)])
            await session.commit()
            service = MarketResearchService(session, 1)
            durations = []
            for _ in range(5):
                started = time.perf_counter()
                result = await service.candidates(scope, limit=100)
                durations.append(round((time.perf_counter()-started)*1000, 2))
                assert len(result["list"]) == 100
                assert all(row["changes"]["want_count"]["value"] == 99 for row in result["list"])
            assert (await MarketResearchService(session, 2).candidates(scope))["list"] == []
            count = await session.scalar(select(func.count()).select_from(MarketObservation))
            assert count == 10000
            print(json.dumps({"passed": True, "mysql_schema_idempotent": True, "observations": count,
                "candidate_items": 100, "query_ms": durations, "median_ms": statistics.median(durations),
                "scope_isolation": True, "production_data": False}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
