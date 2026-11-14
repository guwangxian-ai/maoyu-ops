"""Real Chromium against built files, with private-data-free API fixtures only."""
import asyncio,json,mimetypes
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect

async def main():
    site=Path('/site');evidence=Path('/evidence');evidence.mkdir(exist_ok=True)
    state={'receipt':'unknown','retries':0,'save':None,'error':False};errors=[]
    def receipt():
        return {'state':state['receipt'],'attempts':1,'reason':'模拟回执超时，请先核对聊天' if state['receipt']=='unknown' else None,
                'confirmed_at':'2026-11-06T12:00:00','updated_at':'2026-11-06T12:01:00','can_retry':state['receipt']=='failed',
                'history':[{'number':1,'mid':'fixture-mid','state':state['receipt'],'started_at':'2026-11-06T12:00:00','finished_at':'2026-11-06T12:01:00'}]}
    def order():return {'id':'1','order_id':'test-order','cookie_id':'test-account','item_id':'test-item','item_title':'隔离测试资料',
        'status':'shipped','buyer_id':'test-buyer','quantity':1,'amount':'3.00','delivery_method':'auto',
        'delivery_content':'https://pan.quark.cn/s/isolated-fixture\n提取码 Ab12','delivery_text':receipt()}
    card={'id':1,'name':'隔离测试卡券','type':'text','text_content':'https://pan.quark.cn/s/isolated-fixture\n提取码 Ab12',
          'use_no_logistics_form':True,'send_text_after_form':False,'enabled':True,'delivery_count':0}
    async def route(route):
        u=urlparse(route.request.url);p=u.path;m=route.request.method
        if '/api/' in p:
            if p.endswith('/auth/verify'):data={'authenticated':True,'user_id':1,'username':'isolated','is_admin':True}
            elif p.endswith('/orders/test-order/delivery-text/retry'):
                state['retries']+=1;state['receipt']='pending';data={'success':True,'data':receipt()}
            elif p.endswith('/orders/test-order'):data={'success':False,'message':'模拟读取失败'} if state['error'] else {'success':True,'data':order()}
            elif p.endswith('/orders'):data={'success':True,'data':[order()],'total':1,'page':1,'page_size':20,'total_pages':1}
            elif p.endswith('/cards/1') and m=='PUT':
                state['save']=route.request.post_data_json;card.update(state['save']);data={'success':True,'data':card}
            elif p.endswith('/cards/1'):data=card
            elif p.endswith('/cards'):data={'list':[card],'total':1,'page':1,'page_size':20,'total_pages':1}
            elif '/cookies/' in p:data={'success':True,'data':{'list':[],'total':0},'list':[]}
            elif '/user-settings/' in p:data={'success':True,'value':'true'}
            elif 'system-settings' in p:data={'success':True,'data':{}}
            else:data={'success':True,'data':[],'list':[],'total':0}
            await route.fulfill(status=200,content_type='application/json',body=json.dumps(data));return
        if u.hostname!='delivery.test':await route.fulfill(status=200,body='');return
        f=site/p.removeprefix('/maoyu-ops/').lstrip('/')
        if not f.is_file() or not f.resolve().is_relative_to(site.resolve()):f=site/'index.html'
        await route.fulfill(status=200,content_type=mimetypes.guess_type(str(f))[0] or 'application/octet-stream',body=f.read_bytes())
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        ctx=await browser.new_context(viewport={'width':1365,'height':900})
        await ctx.add_init_script("localStorage.setItem('xianyu-auto-reply:auth_token','isolated-test-token')")
        await ctx.route('**/*',route);page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('https://delivery.test/maoyu-ops/orders')
        await page.get_by_title('查看详情',exact=True).click()
        panel=page.get_by_role('region',name='发货后的文字消息')
        await expect(panel).to_be_visible();await expect(panel.get_by_role('button',name='重试文字发送')).to_have_count(0)
        await expect(panel.get_by_text('发送结果待核实 · 已尝试 1 次')).to_be_visible()
        await page.screenshot(path=str(evidence/'delivery-unknown-desktop.png'),full_page=True)
        state['receipt']='failed';await panel.get_by_role('button',name='刷新记录').click()
        await panel.get_by_role('button',name='重试文字发送').click()
        await expect(panel.get_by_text('等待发送 · 已尝试 1 次')).to_be_visible();assert state['retries']==1
        state['receipt']='success';await panel.get_by_role('button',name='刷新记录').click()
        await expect(panel.get_by_text('平台已确认发送 · 已尝试 1 次')).to_be_visible()
        await expect(panel.get_by_role('button',name='重试文字发送')).to_have_count(0)
        state['error']=True;await panel.get_by_role('button',name='刷新记录').click()
        await expect(page.get_by_text('读取发送记录失败',exact=True)).to_be_visible();state['error']=False
        await page.set_viewport_size({'width':390,'height':844});await page.screenshot(path=str(evidence/'delivery-mobile.png'),full_page=True)
        await page.goto('https://delivery.test/maoyu-ops/cards')
        await page.get_by_title('编辑',exact=True).click()
        toggle=page.get_by_label('发货成功后补发纯文字',exact=False)
        await expect(toggle).to_be_visible();await toggle.check();await expect(toggle).to_be_checked()
        await page.screenshot(path=str(evidence/'delivery-card-mobile.png'),full_page=True)
        await page.get_by_role('button',name='保存',exact=True).click()
        for _ in range(30):
            if state['save']:break
            await asyncio.sleep(.1)
        assert state['save']['send_text_after_form'] and state['save']['use_no_logistics_form']
        assert not errors,errors
        await browser.close()
    print(json.dumps({'passed':True,'desktop':[1365,900],'mobile':[390,844],
        'states':['unknown_no_retry','failed_retry','pending','success','read_error','card_switch_save'],
        'retry_requests':state['retries'],'page_errors':errors,'production_requests':0}))
if __name__=='__main__':asyncio.run(main())
