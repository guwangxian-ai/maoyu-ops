"""Gateway/configuration boundaries; no external messages or operational database."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI, HTTPException
from app.api import api_router
from app.api.routes import orders
from app.mcp.catalog import build_catalog
from app.services.card_service import CardService
from common.models.card import Card

class DeliveryTextApiTests(unittest.IsolatedAsyncioTestCase):
    def test_tools_use_existing_order_scopes_and_exclude_arbitrary_content(self):
        app=FastAPI();app.include_router(api_router,prefix='/api/v1');tools=build_catalog(app.routes)
        get=tools['orders_get_delivery_text'];retry=tools['orders_retry_delivery_text']
        self.assertEqual(get.scope,'delivery:read');self.assertEqual(retry.scope,'delivery:orders:write')
        self.assertTrue(get.readonly);self.assertFalse(retry.readonly)
        self.assertNotIn('body',retry.schema['properties'])
        self.assertIn('operation_id',retry.schema['required'])
        self.assertIn('confirm',retry.schema['required'])
        fields=tools['cards_update_card'].schema['properties']['body']['properties']
        self.assertIn('send_text_after_form',fields)

    async def test_other_owner_cannot_read_or_retry(self):
        service=SimpleNamespace(get_order_by_no=AsyncMock(return_value=SimpleNamespace(owner_id=2)))
        user=SimpleNamespace(id=1)
        with patch.object(orders,'resolve_owner_scope',return_value=(1,False)):
            for action in (orders.get_delivery_text,orders.retry_delivery_text):
                with self.assertRaises(HTTPException) as ctx:await action('order',user,service)
                self.assertEqual(ctx.exception.status_code,404)

    async def test_closed_order_cannot_retry(self):
        service=SimpleNamespace(get_order_by_no=AsyncMock(return_value=SimpleNamespace(owner_id=1,status='refunded')))
        with patch.object(orders,'resolve_owner_scope',return_value=(1,False)):
            result=await orders.retry_delivery_text('order',SimpleNamespace(id=1),service)
        self.assertFalse(result.success)

    async def test_no_plaintext_copy_without_credential_mode(self):
        service=CardService(AsyncMock())
        service.check_card_duplicate=AsyncMock(return_value=None)
        with self.assertRaises(ValueError):
            await service.create_card(user_id=1,name='fixture',card_type='text',send_text_after_form=True)
        with self.assertRaises(ValueError):
            await service.create_card(user_id=1,name='fixture',card_type='api',use_no_logistics_form=True,send_text_after_form=True)

if __name__=='__main__':unittest.main()
