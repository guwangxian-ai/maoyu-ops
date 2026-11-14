"""构建后的真实浏览器验收，所有业务请求使用隔离数据，网络不可访问生产。"""
import asyncio
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect


async def main():
    site, evidence = Path('/site'), Path('/evidence')
    evidence.mkdir(exist_ok=True)
    state = {'registration':'error', 'associated_error':True, 'items_error':False,
             'queries':[], 'saved_items':None, 'saved_channel':None, 'test_notifications':0}
    errors, checks = [], []
    slow_started, release_slow, slow_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    channel = {'id':1, 'name':'隔离账号提醒', 'type':'bark', 'enabled':True,
               'config':{'server_url':'https://isolated.invalid', 'notify_types':['account']}}
    card = {'id':1, 'name':'隔离卡券', 'type':'text', 'enabled':True, 'text_content':'隔离交付内容',
            'use_no_logistics_form':True, 'send_text_after_form':True, 'delivery_count':0}
    async def route(r):
        u=urlparse(r.request.url);p=u.path;m=r.request.method;q=parse_qs(u.query)
        if '/api/' in p:
            if p.endswith('/system-settings/public'):
                if state['registration']=='error':
                    await r.fulfill(status=200,content_type='text/html',body='<html>fixture error</html>');return
                data={'registration_enabled':state['registration']=='open','login_captcha_enabled':False,'show_default_login_info':False}
            elif p.endswith('/auth/verify'):
                data={'authenticated':True,'user_id':1,'username':'isolated','is_admin':True}
            elif p.endswith('/captcha/generate'):
                data={'success':True,'data':{'captcha_image':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','session_id':'isolated'}}
            elif p.endswith('/notification-channels/1/test'):
                state['test_notifications']+=1;data={'success':False,'message':'test must not be called'}
            elif p.endswith('/notification-channels/1') and m=='PUT':
                state['saved_channel']=r.request.post_data_json
                config=state['saved_channel'].get('config')
                channel['config']=json.loads(config) if isinstance(config,str) else config
                data={'success':True,'data':channel}
            elif p.endswith('/notification-channels'):data=[channel]
            elif p.endswith('/message-notifications'):data={'1':[{'id':1,'channel_id':1,'channel_name':channel['name'],'enabled':True,'notify_types':['account']}]}
            elif p.endswith('/cards/1/items') and m=='PUT':
                state['saved_items']=r.request.post_data_json;data={'success':True}
            elif p.endswith('/cards/1/items'):
                data={'success':False,'message':'关联读取失败'} if state['associated_error'] else {'success':True,'data':{'item_ids':['orphan-old']}}
            elif p.endswith('/items/by-card/1'):data={'success':True,'list':[]}
            elif p.endswith('/items/selectable/paginated'):
                keyword=q.get('keyword',[''])[0];state['queries'].append(keyword)
                if keyword=='slow':
                    slow_started.set();await release_slow.wait();slow_finished.set()
                if state['items_error']:data={'success':False,'message':'商品查询失败'}
                else:
                    name='最新查询商品' if keyword=='fast' else '旧查询商品' if keyword=='slow' else '隔离商品'
                    data={'success':True,'data':{'list':[{'item_id':keyword or 'fixture-item','title':name,'price':'3.00'}],
                          'page':1,'page_size':20,'total':1,'total_pages':1,'has_more':False}}
            elif p.endswith('/cards/1'):data=card
            elif p.endswith('/cards'):data={'list':[card],'page':1,'page_size':20,'total':1,'total_pages':1}
            elif '/cookies/' in p:data={'list':[],'total':0,'success':True,'data':{'list':[],'total':0}}
            elif '/user-settings/' in p:data={'success':True,'value':'true'}
            elif 'system-settings' in p:data={}
            else:data={'success':True,'data':[],'list':[],'total':0}
            await r.fulfill(status=200,content_type='application/json',body=json.dumps(data));return
        if u.hostname!='upstream.test':await r.fulfill(status=200,body='');return
        file=site/p.removeprefix('/maoyu-ops/').lstrip('/')
        if not file.is_file() or not file.resolve().is_relative_to(site.resolve()):file=site/'index.html'
        await r.fulfill(status=200,content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream',body=file.read_bytes())

    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        anonymous=await browser.new_context(viewport={'width':390,'height':844})
        await anonymous.route('**/*',route)
        page=await anonymous.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('https://upstream.test/maoyu-ops/register')
        await expect(page.get_by_role('heading',name='注册状态读取失败')).to_be_visible()
        await expect(page.locator('input[type=password]')).to_have_count(0)
        state['registration']='closed';await page.get_by_role('button',name='重新加载').click()
        await expect(page.get_by_role('heading',name='注册功能已关闭')).to_be_visible()
        checks.append('registration settings HTML error is visible and retry resolves to actual disabled state')
        await page.screenshot(path=str(evidence/'registration-mobile.png'),full_page=True)
        state['registration']='open';await page.reload()
        await expect(page.get_by_role('heading',name='用户注册')).to_be_visible()
        await expect(page.locator('input[type=password]')).to_have_count(2)
        checks.append('enabled registration renders form and captcha only after state was read')
        await anonymous.close()

        context=await browser.new_context(viewport={'width':1365,'height':900})
        await context.add_init_script("localStorage.setItem('xianyu-auto-reply:auth_token','isolated-fixture-token')")
        await context.route('**/*',route)
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('https://upstream.test/maoyu-ops/notification-channels')
        await page.get_by_title('编辑',exact=True).click()
        switches=page.get_by_role('switch')
        await expect(switches).to_have_count(3)
        self_labels=await switches.evaluate_all('(elements)=>elements.map(e=>({label:e.getAttribute("aria-label"),checked:e.getAttribute("aria-checked")}))')
        assert sum(x['checked']=='true' for x in self_labels)==1,self_labels
        await switches.nth(0).focus();await page.keyboard.press('Space')
        await expect(switches.nth(0)).to_have_attribute('aria-checked','true')
        await page.screenshot(path=str(evidence/'notification-events-desktop.png'),full_page=True)
        await page.get_by_role('button',name='保存',exact=True).click()
        assert state['saved_channel'] and set(channel['config']['notify_types'])=={'chat','account'}
        assert state['test_notifications']==0
        checks.append('channel event settings roundtrip; switches support keyboard; no test notification sent')

        await page.goto('https://upstream.test/maoyu-ops/cards')
        await page.get_by_title('管理关联商品',exact=True).click()
        modal=page.locator('.modal-content').filter(has=page.get_by_text('待选商品',exact=True))
        await expect(modal.get_by_text('已关联商品加载失败，请关闭后重试',exact=True)).to_be_visible()
        await expect(modal.get_by_role('button',name=re.compile(r'^保存'))).to_be_disabled()
        await modal.get_by_role('button',name='关闭',exact=True).click()
        state['associated_error']=False
        await page.get_by_title('管理关联商品',exact=True).click()
        await expect(modal.get_by_role('button',name=re.compile(r'^保存'))).to_be_enabled()
        await expect(modal.get_by_text('orphan-old',exact=True)).to_be_visible()
        search=modal.get_by_placeholder('搜索商品名称或ID...')
        previous_count=len(state['queries']);await search.fill('typing')
        await page.wait_for_timeout(350)
        assert len(state['queries'])==previous_count
        await search.fill('slow');await search.press('Enter');await asyncio.wait_for(slow_started.wait(),3)
        await search.fill('fast');await search.press('Enter')
        await expect(modal.get_by_text('最新查询商品',exact=True)).to_be_visible()
        release_slow.set();await asyncio.wait_for(slow_finished.wait(),3)
        await page.wait_for_timeout(100)
        await expect(modal.get_by_text('旧查询商品',exact=True)).to_have_count(0)
        await modal.get_by_role('button',name=re.compile(r'^保存')).click()
        assert state['saved_items']=={'item_ids':['orphan-old']},state['saved_items']
        await expect(page.get_by_text('关联商品保存成功',exact=True)).to_have_count(0,timeout=10000)
        checks.append('association load failure blocks save; explicit query ignores stale responses and keeps orphan binding')
        await page.get_by_title('管理关联商品',exact=True).click()
        await expect(modal.get_by_role('button',name=re.compile(r'^保存'))).to_be_enabled()
        await page.set_viewport_size({'width':390,'height':844})
        await page.screenshot(path=str(evidence/'card-selection-mobile.png'),full_page=True)
        assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
        await page.set_viewport_size({'width':1365,'height':900})
        state['items_error']=True
        await modal.get_by_placeholder('搜索商品名称或ID...').fill('fails')
        await modal.get_by_role('button',name='查询',exact=True).click()
        await expect(modal.get_by_text('商品查询失败',exact=True)).to_be_visible()
        await page.screenshot(path=str(evidence/'card-selection-error-desktop.png'),full_page=True)
        checks.append('mobile modal fits viewport; desktop query failure stays visible')
        await context.close();await browser.close()
    assert not errors,errors
    print(json.dumps({'groups_passed':len(checks),'checks':checks,'browser_errors':errors,'external_calls':0},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
