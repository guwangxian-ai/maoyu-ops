"""Additive schema, invoked under the existing database initialization lock."""
from common.models.delivery_text import DeliveryTextAttempt, DeliveryTextReceipt


async def ensure_delivery_text_schema(conn):
    for model in (DeliveryTextReceipt, DeliveryTextAttempt):
        await conn.run_sync(lambda sync_conn, table=model.__table__: table.create(sync_conn, checkfirst=True))
