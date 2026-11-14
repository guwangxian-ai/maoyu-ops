"""Additive, idempotent research schema; no changes to operational tables."""
from common.models.market_observation import MarketMonitorState, MarketObservation, MarketSampleRun


async def ensure_market_schema(conn):
    for model in (MarketSampleRun, MarketObservation, MarketMonitorState):
        await conn.run_sync(lambda sync_conn, table=model.__table__: table.create(sync_conn, checkfirst=True))
