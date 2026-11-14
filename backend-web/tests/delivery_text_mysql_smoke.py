"""Disposable MySQL + simulated platform. Never run against operational data."""
import asyncio
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select, func, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from common.models.card import Card
from common.models.card_item_relation import CardItemRelation
from common.models.xy_order import XYOrder
from common.models.delivery_text import DeliveryTextReceipt, DeliveryTextAttempt
from common.db.delivery_text_schema import ensure_delivery_text_schema
from common.services.delivery_text_service import DeliveryTextService, get_beijing_now_naive

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('delivery_text_worker', ROOT/'websocket/app/services/xianyu/delivery_text.py')
worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
CONTENT = '资料已发货，可复制链接打开：\nhttps://pan.quark.cn/s/isolated-fixture\n提取码：Ab12'

async def main():
    assert os.environ.get('DELIVERY_ISOLATED_TEST') == '1'
    assert os.environ['MYSQL_DATABASE'] == 'delivery_isolated_test'
    assert os.environ['MYSQL_HOST'] == '127.0.0.1'
    engine = create_async_engine('mysql+asyncmy://root:'+os.environ['MYSQL_PASSWORD']+'@127.0.0.1/delivery_isolated_test')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    worker.async_session_maker = factory
    checks = []
    async def fixture(status='paid', enabled=True, amount=3, buyer='buyer'):
        async with factory() as s:
            for model in (DeliveryTextAttempt, DeliveryTextReceipt, CardItemRelation, XYOrder, Card):
                await s.execute(delete(model))
            s.add(Card(id=1, user_id=1, item_id='item', name='fixture', type='text', enabled=enabled,
                       text_content=CONTENT, use_no_logistics_form=True, send_text_after_form=True, delivery_count=0))
            s.add(XYOrder(id=1, owner_id=1, account_id='account', order_no='order', item_id='item',
                          status=status, amount=amount, buyer_id=buyer, chat_id='chat'))
            await s.commit()
    async def prepare():
        async with factory() as s:
            row, new = await DeliveryTextService(s).prepare('account','order',1,'buyer','chat',CONTENT)
            return row.id,new
    async def pending():
        rid,_ = await prepare()
        async with factory() as s:
            await DeliveryTextService(s).shipping_result(rid,confirmed=True)
        return rid
    async def read():
        async with factory() as s:
            return await DeliveryTextService(s).get('account','order',1)
    def parent(response='success', connected=True):
        p=SimpleNamespace(cookie_id='account',ws=SimpleNamespace(closed=False) if connected else None,
            _pending_mid_futures={}, calls=[], tasks=[], is_auto_confirm_enabled=lambda:True,
            is_only_send_card_enabled=lambda:False,
            auto_delivery_handler=SimpleNamespace(auto_confirm=AsyncMock(return_value={'success':True,'message':'SUCCESS::调用成功'})))
        def reject(x): return (x.get('body') or {}).get('reason')
        p._extract_send_reject_reason=reject
        async def send(ws,chat,buyer,content,**kw):
            p.calls.append((chat,buyer,content,kw))
            f=asyncio.get_running_loop().create_future();p._pending_mid_futures[kw['message_id']]=f
            if response=='success':f.set_result({'code':200})
            elif response=='rejected':f.set_result({'code':400,'body':{'reason':'CSI_FORBID'}})
            elif response=='unrecognized':f.set_result({'body':{}})
            elif response=='exception':raise ConnectionError('after uncertain socket write')
            return {'success':True,'send_future':f,'mid':kw['message_id']}
        p.send_msg=send
        def track(coro):
            task=asyncio.create_task(coro);p.tasks.append(task);return task
        p._create_tracked_task=track
        return p
    try:
        async with engine.begin() as c:
            for model in (Card, CardItemRelation, XYOrder):
                await c.run_sync(lambda sync,m=model: m.__table__.create(sync,checkfirst=True))
            await ensure_delivery_text_schema(c);await ensure_delivery_text_schema(c)
        checks.append('mysql idempotent schema')
        for status in ['pending_payment','closed','refund','refunded','shipped','completed']:
            await fixture(status)
            try: await prepare()
            except ValueError: pass
            else: raise AssertionError(status)
        checks.append('unpaid/refund/closed/historical orders rejected')
        for kwargs in [dict(enabled=False),dict(amount=0),dict(buyer='other')]:
            await fixture(**kwargs)
            try: await prepare()
            except ValueError: pass
            else: raise AssertionError(kwargs)
        checks.append('switch/payment/recipient guards')
        await fixture()
        start=time.perf_counter()
        prepared=await asyncio.gather(*(prepare() for _ in range(12)))
        assert sum(new for _,new in prepared)==1
        assert len({rid for rid,_ in prepared})==1
        checks.append('12 concurrent preparations -> one immutable receipt')
        rid=prepared[0][0]
        async with factory() as s: await DeliveryTextService(s).shipping_result(rid,confirmed=True)
        p=parent()
        await asyncio.gather(*(worker.dispatch_receipt(rid,p,ack_timeout=.05) for _ in range(12)))
        assert len(p.calls)==1 and p.calls[0][:3]==('chat','buyer',CONTENT)
        assert p.calls[0][3]['log_content'] is False
        assert (await read())['state']=='success'
        checks.append('12 concurrent sends -> one plaintext message, explicit ACK')
        async with factory() as s:
            await DeliveryTextService(s).retry('account','order',1)
            assert await s.scalar(select(Card.delivery_count).where(Card.id==1))==1
        await worker.dispatch_receipt(rid,p,ack_timeout=.05)
        assert len(p.calls)==1
        checks.append('replay does not send/confirm/consume another card')
        concurrency_ms=round((time.perf_counter()-start)*1000,2)
        for response,expected in [('rejected','failed'),('timeout','unknown'),('unrecognized','unknown'),('exception','unknown')]:
            await fixture();rid=await pending();p=parent(response)
            await worker.dispatch_receipt(rid,p,ack_timeout=.01)
            receipt=await read();assert receipt['state']==expected,(response,receipt)
            await worker.dispatch_receipt(rid,p,ack_timeout=.01);assert len(p.calls)==1
            if expected=='unknown':
                async with factory() as s:
                    try:await DeliveryTextService(s).retry('account','order',1)
                    except ValueError:pass
                    else:raise AssertionError('unknown retried')
            checks.append(response+' maps to '+expected+' without blind retry')
        await fixture();rid=await pending();p=parent('rejected')
        for attempt in range(1,4):
            await worker.dispatch_receipt(rid,p,ack_timeout=.01)
            assert (await read())['attempts']==attempt
            if attempt<3:
                async with factory() as s:await DeliveryTextService(s).retry('account','order',1)
        assert len((await read())['history'])==3 and not (await read())['can_retry']
        checks.append('explicit rejected-message retry limited to 3 with full history')
        await fixture();rid=await pending()
        async with factory() as s:
            await s.execute(update(Card).where(Card.id==1).values(text_content='changed-after-shipping'))
            await s.commit()
        p=parent();await worker.dispatch_receipt(rid,p)
        assert p.calls[0][2]==CONTENT
        checks.append('content frozen at shipping; later card edits do not change delivery')
        for changed in ('refund','switch','buyer','chat'):
            await fixture();rid=await pending()
            async with factory() as s:
                if changed=='switch':await s.execute(update(Card).values(send_text_after_form=False))
                else:await s.execute(update(XYOrder).values(**{'refund':{'status':'refunded'},'buyer':{'buyer_id':'different'},'chat':{'chat_id':'different'}}[changed]))
                await s.commit()
            p=parent();await worker.dispatch_receipt(rid,p)
            assert not p.calls and (await read())['state']=='cancelled'
        checks.append('refund/switch/buyer/chat changes block sending')
        await fixture();rid=await pending();p=parent(connected=False)
        await worker.dispatch_receipt(rid,p)
        assert (await read())['attempts']==0 and (await read())['state']=='pending'
        async with factory() as s:
            await s.execute(update(DeliveryTextReceipt).values(next_attempt_at=get_beijing_now_naive()-timedelta(seconds=1)))
            await s.commit()
        p=parent();await worker.dispatch_receipt(rid,p)
        assert len(p.calls)==1 and (await read())['state']=='success'
        checks.append('offline queue survives and dispatches once on reconnect')
        await fixture();rid=await pending()
        async with factory() as s:
            await DeliveryTextService(s).claim(rid,'crash-mid')
            await s.execute(update(DeliveryTextReceipt).values(updated_at=get_beijing_now_naive()-timedelta(minutes=10)))
            await s.execute(update(DeliveryTextAttempt).values(started_at=get_beijing_now_naive()-timedelta(minutes=10)))
            await s.commit();await DeliveryTextService(s).recover_stale()
        p=parent();await worker.dispatch_receipt(rid,p)
        assert not p.calls and (await read())['state']=='unknown'
        checks.append('crashed sending lease becomes unknown, never resent')
        await fixture();p=parent()
        kwargs=dict(order_no='order',item_id='item',card_id=1,buyer_id='buyer',chat_id='chat',content=CONTENT)
        result=await worker.deliver_form_with_text(p,**kwargs)
        await asyncio.gather(*p.tasks)
        await worker.deliver_form_with_text(p,**kwargs)
        p.auto_delivery_handler.auto_confirm.assert_awaited_once_with('order','item',trade_text=CONTENT)
        assert len(p.calls)==1 and result['platform_shipping_confirmed']
        async with factory() as s:
            order=await s.get(XYOrder,1);assert order.delivery_content==CONTENT and order.status=='shipped'
            assert await DeliveryTextService(s).get('account','order',999) is None
            try:await DeliveryTextService(s).retry('account','order',999)
            except ValueError:pass
            else:raise AssertionError('cross owner retry')
        checks.append('shared end-to-end helper: credential retained then plaintext, owner isolation')
        for result in ({'success':False},{'success':True,'already_delivered':True},{'success':True,'skipped_only_send_card':True}):
            await fixture();p=parent();p.auto_delivery_handler.auto_confirm.return_value=result
            try:await worker.deliver_form_with_text(p,**kwargs)
            except ValueError:pass
            else:raise AssertionError('unproven credential accepted')
            assert not p.calls and not p.tasks and (await read())['state']=='shipping_unknown'
        checks.append('confirmation failure/already shipped do not send fresh content')
        print(json.dumps({'passed':True,'checks':checks,'checks_count':len(checks),'concurrent_prepare_send_ms':concurrency_ms,
                          'real_mysql':True,'platform_simulated':True,'production_data':False}))
    finally:await engine.dispose()

if __name__=='__main__':asyncio.run(main())
