"""真实 MySQL 隔离验证；无生产数据库、授权卷或平台网络。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import statistics
import time

import httpx
from fastapi import FastAPI
from sqlalchemy import event, insert, select, text

from app.api import deps
from app.api.routes import selectable_items
from app.services.selectable_item_service import SelectableItemService
from app.services.system_setting_service import SystemSettingService
from common.db.init_database import DatabaseInitializer
from common.db.item_query_indexes import ensure_item_query_indexes
from common.db.session import async_session_maker as factory, async_engine as engine
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.default_reply import DefaultReply
from common.models.message_notification import MessageNotification
from common.models.notification_channel import NotificationChannel
from common.models.system_setting import SystemSetting
from common.models.user import UserRole
from common.models.xy_account import XYAccount
from common.models.xy_catalog_item import XYCatalogItem
from common.services import item_service as item_module
from common.services.item_service import ItemService


async def main():
    assert os.environ.get('UPSTREAM_ISOLATED_TEST') == '1'
    assert os.environ.get('MYSQL_DATABASE') == 'upstream_20261114_test'
    models = [XYAccount, XYCatalogItem, DefaultReply, Card, CardItemRelation,
              MessageNotification, NotificationChannel, SystemSetting]
    checks = []
    async with engine.begin() as conn:
        for model in reversed(models): await conn.run_sync(lambda c, m=model: m.__table__.drop(c, checkfirst=True))
        for model in models: await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    now = datetime(2026, 11, 14, 12)
    async with factory() as s:
        s.add_all([XYAccount(id=n, owner_id=1 if n < 3 else 2, account_id=str(n), cookie='isolated', login_method='qr') for n in [1, 2, 3]])
        for i in range(240):
            metadata = {'description': 'fixture full detail keyword' if i == 5 else 'fixture',
                        'is_multi_spec': i % 3 == 0, 'multi_quantity_delivery': i % 4 == 0}
            if i % 3 != 2:
                metadata['sale_snapshot'] = {'status': 'on_sale' if i % 3 == 0 else 'pending_verification', 'checked_at': now.isoformat()}
            s.add(XYCatalogItem(id=i+1, owner_id=1 if i < 200 else 2, account_pk=(1 if i % 2 == 0 else 2) if i < 200 else 3,
                                item_id='item'+str(i), title='资料'+str(i), price='3.00', metadata_json=metadata,
                                created_at=now-timedelta(seconds=i), updated_at=now))
        s.add(XYCatalogItem(id=241, owner_id=1, account_pk=999, item_id='orphan', title='孤立账号商品', price='3', metadata_json={}, created_at=now-timedelta(days=1), updated_at=now))
        s.add_all([DefaultReply(id=1, account_id='1', item_id='item0', enabled=True), DefaultReply(id=2, account_id='1', item_id='item0', enabled=False)])
        s.add(Card(id=1, user_id=1, name='fixture', type='text', enabled=True, item_id='item7', text_content='isolated delivery', send_text_after_form=True))
        s.add(CardItemRelation(id=1, user_id=1, card_id=1, item_id='item0'))
        s.add(SystemSetting(key='registration_enabled', value='true'))
        await s.commit()
    namespace = dict(vars(item_module))
    exec(Path('/baseline_list_page.py').read_text(), namespace)
    baseline = namespace['list_items_paginated']
    cases = [dict(owner_id=1), dict(owner_id=2), dict(owner_id=None), dict(owner_id=1, account_id='1'),
             dict(owner_id=2, account_id='1'), dict(owner_id=1, keyword='full detail keyword'),
             dict(owner_id=1, sale_status='on_sale'), dict(owner_id=1, sale_status='pending_verification'),
             dict(owner_id=1, sale_status='unchecked'), dict(owner_id=1, is_multi_spec=False),
             dict(owner_id=1, multi_quantity_delivery=True), dict(owner_id=1, page=999)]
    for params in cases:
        async with factory() as s:
            service = ItemService(s)
            old = await baseline(service, **params)
            new = await service.list_items_paginated(**params)
            assert old == new, ('pagination changed', params, old, new)
    checks.append('12 pagination cases preserve ownership, sale snapshots, full-detail search, flags, cards and replies')

    counts = []
    counter = [0]
    def sql_count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'): counter[0] += 1
    event.listen(engine.sync_engine, 'before_cursor_execute', sql_count)
    timings = {}
    for name, function in [('before', baseline), ('after', None)]:
        samples = []
        for run in range(7):
            async with factory() as s:
                service = ItemService(s)
                counter[0] = 0
                start = time.perf_counter()
                if function: await function(service, owner_id=1, page_size=20)
                else: await service.list_items_paginated(owner_id=1, page_size=20)
                elapsed = (time.perf_counter()-start)*1000
                if run: samples.append(elapsed)
                if run == 6: counts.append(counter[0])
        timings[name+'_median_ms'] = round(statistics.median(samples), 2)
    event.remove(engine.sync_engine, 'before_cursor_execute', sql_count)
    assert counts[1] == 2 and counts[0] > counts[1], counts
    timings.update(before_selects=counts[0], after_selects=counts[1], catalog_rows=241, page_size=20, measured_repeats=6)
    checks.append('pagination SQL round trips measured before and after on identical 241-row fixture')

    async with factory() as s:
        # Duplicate same-owner item IDs across accounts must not block the last page or all-select.
        s.add(XYCatalogItem(owner_id=1, account_pk=2, item_id='item0', title='另一账号同标识', price='3', metadata_json={}, created_at=now+timedelta(seconds=1)))
        await s.commit()
        service = SelectableItemService(s)
        rows = []
        for page in range(1, 30):
            data = await service.get_page(1, page=page, page_size=20)
            rows.extend(data['list'])
            assert all(set(row) == {'item_id','title','price'} for row in data['list'])
            if not data['has_more']: break
        else: raise AssertionError('pagination did not terminate')
        assert len(rows) == 202 and len({r['item_id'] for r in rows}) == 201
        all_rows = await service.get_all_selectable_item_keys(1)
        assert {r['item_id'] for r in all_rows} == {r['item_id'] for r in rows}
        assert not (await service.get_page(1, search='資料%_missing'))['list']
        assert not (await service.get_page(1, search='item230'))['list']
    checks.append('lightweight pages, all-select, duplicate IDs, literal wildcards and owner isolation')

    app = FastAPI()
    app.include_router(selectable_items.router, prefix='/api/v1')
    async def database():
        async with factory() as s: yield s
    app.dependency_overrides[deps.get_db_session] = database
    app.dependency_overrides[deps.get_current_active_user] = lambda: type('User', (), {'id': 1, 'role': UserRole.MEMBER})()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
        response = await client.get('/api/v1/items/selectable/paginated', params={'keyword':'item230'})
        assert response.status_code == 200 and response.json()['data']['total'] == 0
        assert (await client.get('/api/v1/items/selectable/paginated', params={'page_size':101})).status_code == 422
    checks.append('real lightweight HTTP route enforces owner scope and 100-row limit')

    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE xy_catalog_items DROP INDEX idx_cat_created_id, DROP INDEX idx_cat_account_created_id'))
        await conn.execute(text('SET SESSION lock_wait_timeout=5'))
        await ensure_item_query_indexes(conn)
        await ensure_item_query_indexes(conn)
        names = set((await conn.execute(text('SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME="xy_catalog_items"'))).scalars())
        assert {'idx_cat_created_id','idx_cat_account_created_id'}.issubset(names)
    checks.append('missing legacy MySQL indexes added online and second migration is idempotent')

    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE xy_message_notifications DROP COLUMN notify_types'))
        await conn.execute(text("INSERT INTO xy_message_notifications (owner_id,account_pk,account_identifier,channel_id,enabled) VALUES (1,1,'1',1,1)"))
    initializer = DatabaseInitializer()
    initializer.COLUMN_MIGRATIONS = {'xy_message_notifications': DatabaseInitializer.COLUMN_MIGRATIONS['xy_message_notifications']}
    await initializer.migrate_columns()
    await initializer.migrate_columns()
    async with factory() as s:
        row = (await s.execute(select(MessageNotification))).scalar_one()
        assert row.notify_types == 'chat,delivery,account'
    checks.append('legacy notification row receives default-all column without losing subscription')

    async def defaults():
        async with factory() as s: return await SystemSettingService(s).list_settings()
    results = await asyncio.gather(defaults(), defaults())
    assert all(d['registration_enabled'] == 'true' for d in results)
    async with factory() as s:
        assert (await s.execute(select(SystemSetting.value).where(SystemSetting.key=='registration_enabled'))).scalar_one() == 'true'
    checks.append('concurrent default initialization preserves administrator registration choice')
    await engine.dispose()
    print(json.dumps({'checks':checks,'groups_passed':len(checks),'performance':timings,'database':'isolated MySQL','external_calls':0}, ensure_ascii=False))


if __name__ == '__main__': asyncio.run(main())
