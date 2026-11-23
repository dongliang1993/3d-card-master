"""Browser checks for the user's local preview; requires pytest + Playwright Chromium."""
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

TEMPLATE = Path(__file__).resolve().parents[1] / 'assets/template'
NOTE = '愿你日日有花。\n<em>写给妈妈</em>'


@pytest.fixture
def preview(tmp_path):
    subprocess.run([sys.executable, str(TEMPLATE.parents[1] / 'scripts/create_card.py'), str(tmp_path),
                    '--title', '春日来信', '--greeting', '亲爱的妈妈，', '--message', NOTE,
                    '--signature', '爱你的我'], check=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=tmp_path))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('software_fallback', [False, True])
def test_open_read_and_close_without_external_requests(preview, software_fallback):
    with sync_playwright() as browser_tools:
        browser = browser_tools.chromium.launch(args=['--disable-webgl'] if software_fallback else ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'])
        page = browser.new_page(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        remote = []
        page.on('request', lambda request: remote.append(request.url) if urlsplit(request.url).hostname not in (None, '127.0.0.1') else None)
        response = page.goto(preview + 'index.html')
        assert response.ok, 'Missing local preview entry point'
        expect(page).to_have_title('春日来信')
        expect(page.locator('main')).to_have_attribute('data-renderer', 'flat' if software_fallback else 'webgl', timeout=30000)
        expect(page.get_by_text(NOTE, exact=True)).to_be_hidden()
        page.get_by_role('button', name='翻开贺卡').focus()
        page.keyboard.press('Enter')
        expect(page.get_by_text('亲爱的妈妈，')).to_be_visible()
        expect(page.get_by_text(NOTE, exact=True)).to_be_visible()
        if not software_fallback:
            page.wait_for_function("""() => {
              const paper = document.querySelector('#letter').getBoundingClientRect();
              const stage = document.querySelector('#stage').getBoundingClientRect();
              return paper.width > 60 && paper.left >= stage.left && paper.right <= stage.right;
            }""", timeout=8000)
        page.keyboard.press('Shift+Tab')
        expect(page.locator('.writing')).to_be_focused()
        page.get_by_text('亲爱的妈妈，').click()
        expect(page.get_by_role('button', name='合上贺卡')).to_be_visible()
        page.get_by_role('button', name='合上贺卡').click()
        expect(page.get_by_text(NOTE, exact=True)).to_be_hidden()
        box = page.locator('canvas' if not software_fallback else '.proof').bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 30, y + 6, steps=4)
        page.mouse.up()
        expect(page.get_by_text(NOTE, exact=True)).to_be_hidden()
        page.mouse.click(x, y)
        expect(page.get_by_text(NOTE, exact=True)).to_be_visible()
        assert not remote
        browser.close()


def test_preview_blocks_nonlocal_assets_even_after_customization(preview):
    with sync_playwright() as tools:
        browser = tools.chromium.launch()
        page = browser.new_page()
        escaped = []
        page.route('https://outside.invalid/**', lambda route: (escaped.append(route.request.url), route.abort()))
        page.goto(preview + 'index.html')
        page.evaluate("""() => new Promise(resolve => {
          const pic = new Image(); pic.onload = pic.onerror = () => resolve();
          pic.src = 'https://outside.invalid/cover.png';
        })""")
        assert not escaped, 'External image left the preview origin'
        browser.close()


REPO_URL = 'https://github.com/dongliang1993/3d-card-master'


@pytest.mark.parametrize('width', [320, 390, 1280])
def test_credit_link_is_shown_by_default_without_requests(preview, width):
    with sync_playwright() as tools:
        browser = tools.chromium.launch(args=['--disable-webgl'])
        page = browser.new_page(viewport={'width': width, 'height': 800})
        remote = []
        page.on('request', lambda request: remote.append(request.url) if urlsplit(request.url).hostname not in (None, '127.0.0.1') else None)
        page.goto(preview + 'index.html')
        credit = page.locator('#credit')
        expect(credit).to_be_visible()
        expect(credit).to_have_attribute('href', REPO_URL)
        credit_box = credit.bounding_box()
        dock_box = page.locator('.dock').bounding_box()
        overlaps = not (credit_box['x'] + credit_box['width'] <= dock_box['x']
                        or dock_box['x'] + dock_box['width'] <= credit_box['x']
                        or credit_box['y'] + credit_box['height'] <= dock_box['y']
                        or dock_box['y'] + dock_box['height'] <= credit_box['y'])
        assert not overlaps, 'Credit overlaps the dock'
        assert not remote
        browser.close()


def test_credit_can_be_turned_off_in_card_json(preview, tmp_path):
    import json
    card_path = tmp_path / 'card.json'
    card = json.loads(card_path.read_text(encoding='utf-8'))
    card_path.write_text(json.dumps({**card, 'credit': False}, ensure_ascii=False), encoding='utf-8')
    with sync_playwright() as tools:
        browser = tools.chromium.launch(args=['--disable-webgl'])
        page = browser.new_page()
        page.goto(preview + 'index.html')
        expect(page.locator('main')).to_have_attribute('data-renderer', 'flat', timeout=30000)
        expect(page.locator('#credit')).to_have_count(0)
        browser.close()
