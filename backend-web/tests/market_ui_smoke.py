"""Built frontend in real Chromium; all HTTP responses are isolated fixtures."""
import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, expect

SITE = Path('/site')
OUTPUT = Path('/evidence')
SCOPE = 'a' * 64


async def main():
    assert SITE.joinpath('index.html').is_file()
    OUTPUT.mkdir(exist_ok=True)
    runs, requests, errors = [], [], []
    mode = {'fail': False, 'abort_next': False, 'delay': False}
    release = asyncio.Event()
    def batch(key):
        return {'id': str(len(runs)+1), 'sample_key': key, 'scope_key': SCOPE, 'keyword': '表格',
            'status': 'success', 'source': 'manual_search', 'monitor_task_id': None,
            'started_at': '2026-11-04T10:00:00+08:00', 'finished_at': '2026-11-04T10:00:02+08:00',
            'item_count': 1, 'pages_completed': 1, 'pages_requested': 1, 'message': None}
    def observation():
        return {'id': len(runs), 'run_id': runs[-1]['id'], 'scope_key': SCOPE, 'item_id': '123',
            'title': '自制表格（隔离测试样本）', 'item_url': 'https://www.goofish.com/item?id=123',
            'price': 3, 'price_source': 'search.list_price', 'want_count': 10 + 4 * (len(runs)-1),
            'want_source': 'search.serviceUtParams', 'want_approximate': False, 'browse_count': None,
            'browse_source': None, 'observed_at': '2026-11-04T10:00:00+08:00'}
    async def route_handler(route):
        url = urlparse(route.request.url); path = url.path
        if '/api/' in path:
            requests.append(path)
            if path.endswith('/auth/verify'):
                data = {'authenticated': True, 'user_id': 1, 'username': 'isolated', 'is_admin': True}
            elif path.endswith('/cookies/options'):
                data = [{'pk': 1, 'id': 'mine', 'enabled': True, 'remark': '隔离测试账号'}]
            elif '/user-settings/' in path:
                data = {'success': True, 'value': 'true'}
            elif path.endswith('/market-research/samples') and route.request.method == 'POST':
                body = route.request.post_data_json
                if mode['delay']:
                    await release.wait()
                existing = next((r for r in runs if r['sample_key'] == body['sample_key']), None)
                if existing is None:
                    existing = batch(body['sample_key']); runs.append(existing)
                if mode['abort_next']:
                    mode['abort_next'] = False
                    await route.abort(); return
                data = {'success': True, 'code': 200, 'data': {'run': existing, 'replayed': existing is not runs[-1]}}
            elif path.endswith('/market-research/samples'):
                data = {'success': False, 'message': '模拟查询失败'} if mode['fail'] else {'success': True, 'data': {'list': list(reversed(runs)), 'total': len(runs), 'page': 1, 'page_size': 20}}
            elif path.endswith('/market-research/monitor-states'):
                data = {'success': True, 'data': {'list': [], 'total': 0, 'page': 1, 'page_size': 100}}
            elif path.endswith('/market-research/candidates'):
                item = observation()
                item.update(sample_count=len(runs), first_observed_at='2026-11-03T10:00:00+08:00',
                    candidate_signal='filtered_out' if parse_qs(url.query).get('exclude_terms') else 'needs_review',
                    changes={'want_count': {'value': 4 if len(runs)>1 else None, 'reason': None if len(runs)>1 else 'need_two_samples'},
                             'price': {'value': 0, 'reason': None}, 'browse_count': {'value': None, 'reason': 'missing_value'}})
                data = {'success': True, 'data': {'list': [item], 'run': runs[-1], 'latest_attempt': runs[-1],
                    'summary': {'sample_items': 1, 'known_want_items': 1, 'price_samples': 1, 'median_asking_price': 3}}}
            elif '/market-research/history/' in path:
                data = {'success': True, 'data': {'list': [observation()], 'total': 1, 'page': 1, 'page_size': 50}}
            elif 'system-settings' in path:
                data = {'success': True, 'data': {}}
            else:
                data = {'success': True, 'data': [], 'list': [], 'total': 0}
            await route.fulfill(status=200, content_type='application/json', body=json.dumps(data)); return
        if url.hostname != 'market.test':
            await route.fulfill(status=200, body=''); return
        relative = path.removeprefix('/maoyu-ops/').lstrip('/')
        file = SITE / relative
        if not file.is_file() or not file.resolve().is_relative_to(SITE.resolve()):
            file = SITE / 'index.html'
        await route.fulfill(status=200, content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream', body=file.read_bytes())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = await browser.new_context(viewport={'width': 1365, 'height': 900})
        await context.add_init_script("localStorage.setItem('xianyu-auto-reply:auth_token','isolated-test-token')")
        await context.route('**/*', route_handler)
        page = await context.new_page()
        def capture_error(err):
            errors.append(str(err)); (OUTPUT/'page-errors.json').write_text(json.dumps(errors))
        page.on('pageerror', capture_error)
        await page.goto('https://market.test/maoyu-ops/product-monitor/research')
        assert await page.evaluate("isSecureContext && typeof crypto.randomUUID === 'function'")
        await expect(page.get_by_role('heading', name='竞品研究', exact=True)).to_have_count(1)
        await expect(page.get_by_text('暂无采样记录。', exact=False)).to_be_visible()
        await page.screenshot(path=str(OUTPUT/'research-empty.png'), full_page=True)
        await page.get_by_label('搜索关键词', exact=True).fill('表格')
        mode['delay'] = True
        await page.get_by_role('button', name='采样一页', exact=True).click()
        await expect(page.get_by_role('button', name='采样中…', exact=True)).to_be_disabled()
        release.set(); mode['delay'] = False
        await expect(page.get_by_text('待第二次采样', exact=True)).to_be_visible()
        await page.get_by_role('button', name='采样一页', exact=True).click()
        await expect(page.get_by_role('cell', name='+4', exact=True)).to_be_visible()
        await page.get_by_role('button', name='看历史', exact=True).click()
        await expect(page.get_by_role('heading', name='观测历史', exact=True)).to_be_visible()
        await page.screenshot(path=str(OUTPUT/'research-desktop.png'), full_page=True)
        await page.get_by_label('排除词', exact=True).fill('表格')
        await page.get_by_role('button', name='应用筛选', exact=True).click()
        await expect(page.get_by_text('不符合关键词筛选', exact=True)).to_be_visible()
        mode['fail'] = True
        await page.get_by_role('button', name='刷新记录', exact=True).click()
        await expect(page.get_by_role('alert').filter(has_text='模拟查询失败')).to_be_visible()
        mode['fail'] = False
        mode['abort_next'] = True
        await page.get_by_role('button', name='采样一页', exact=True).click()
        await expect(page.get_by_role('button', name='查看本轮结果', exact=True)).to_be_enabled()
        count = len(runs)
        await page.reload()
        await expect(page.get_by_role('button', name='查看本轮结果', exact=True)).to_be_enabled()
        await page.get_by_role('button', name='查看本轮结果', exact=True).click()
        await expect(page.get_by_role('button', name='采样一页', exact=True)).to_be_enabled()
        assert len(runs) == count, 'Interrupted request was collected twice'
        await page.set_viewport_size({'width': 390, 'height': 844})
        # The shared layout animates its desktop margin and drawer on resize.
        # Assert the settled geometry; overflow alone misses a squeezed page.
        await page.wait_for_function("document.querySelector('main').getBoundingClientRect().left <= 1 && document.querySelector('aside').getBoundingClientRect().right <= 1")
        keyword_box = await page.get_by_label('搜索关键词', exact=True).bounding_box()
        assert keyword_box and keyword_box['width'] >= 300, 'Mobile form is squeezed by the sidebar'
        await page.screenshot(path=str(OUTPUT/'research-mobile.png'), full_page=True)
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'Mobile page overflows viewport'
        await page.keyboard.press('Tab')
        assert await page.evaluate("document.activeElement.tagName !== 'BODY'")
        assert not errors, errors
        result = {'passed': True, 'desktop': [1365, 900], 'mobile': [390, 844], 'duplicate_heading': False,
                  'states': ['empty', 'loading', 'success', 'error', 'unknown', 'history', 'filter', 'interruption_resume'],
                  'no_duplicate_sample_on_resume': True, 'page_errors': errors,
                  'external_network': False, 'production_credentials': False}
        (OUTPUT/'ui-result.json').write_text(json.dumps(result, indent=2))
        await browser.close()
        print(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main())
