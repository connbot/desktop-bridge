#!/usr/bin/env python3
"""Record a genuine, deterministic Desktop Bridge capability demonstration.

This is a rehearsed MCP test driver, not an autonomous-model benchmark. Every
workspace mutation goes through the real Coding Tools MCP integration; every
app interaction goes through Desktop Bridge browser/desktop tools. Playwright
on the test runner only observes and records the existing public noVNC viewer.
All inputs are fictional sample data. No third-party actions or live claims.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'demo' / 'personal-workspace'
OWNER = os.getenv('BRIDGE_DEMO_OWNER_TOKEN', 'test-only-owner-token-not-for-deployment-123')
WIDTH, HEIGHT = 1600, 1200


def unpack(result):
    texts = [content.text for content in result.content if content.type == 'text']
    try:
        return json.loads(texts[0])
    except (IndexError, ValueError):
        return {'text': '\n'.join(texts)}


async def authenticate(http, url):
    response = await http.post('/api/login', json={'token': OWNER})
    response.raise_for_status()
    csrf = response.json()['csrf']
    response = await http.post('/register', json={
        'client_name': 'Rehearsed personal capability demo',
        'redirect_uris': ['http://127.0.0.1:43111/callback'],
    })
    response.raise_for_status()
    client = response.json()
    verifier = 'capability-demo-' + 'x' * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {
        'client_id': client['client_id'], 'redirect_uri': client['redirect_uris'][0],
        'response_type': 'code', 'code_challenge_method': 'S256',
        'code_challenge': challenge, 'resource': url + '/mcp', 'state': 'sample-demo',
    }
    response = await http.post('/authorize', data={**params, 'csrf': csrf})
    assert response.status_code == 303, response.status_code
    code = parse_qs(urlsplit(response.headers['location']).query)['code'][0]
    response = await http.post('/token', data={
        'grant_type': 'authorization_code', 'client_id': client['client_id'],
        'redirect_uri': params['redirect_uri'], 'code': code,
        'code_verifier': verifier, 'resource': url + '/mcp',
    })
    response.raise_for_status()
    return csrf, response.json()['access_token']


def initial_files():
    targets = {
        'index.html': 'tools/index.html', 'style.css': 'tools/style.css', 'app.js': 'tools/app.js',
        'sample-spending.csv': 'data/sample-spending.csv', 'analyze.py': 'scripts/analyze.py',
        'serve.py': 'scripts/serve.py',
        'weekend-plan.md': 'plans/weekend-plan.md',
        'packing-checklist.md': 'plans/packing-checklist.md',
    }
    return {f'everyday/{target}': (ASSETS / source).read_text() for source, target in targets.items()}


def add_files_patch(files):
    lines = ['*** Begin Patch']
    for path, content in files.items():
        lines.append('*** Add File: ' + path)
        lines.extend('+' + line for line in content.splitlines())
    return '\n'.join(lines + ['*** End Patch'])


def rainy_day_patch(files):
    old = next(line for line in files['everyday/tools/index.html'].splitlines()
               if 'id="free-afternoon"' in line)
    new = old.replace('</button></section>', '</button><button class="text-button" '
                      'id="rainy-afternoon" aria-label="Plan a rainy afternoon">'
                      'Plan a rainy afternoon <span>☂</span></button></section>')
    assert new != old
    js = '''
$('rainy-afternoon').addEventListener('click', () => {
  activityCost = 18;
  freeAfternoon = false;
  $('afternoon-title').textContent = 'Find a cozy neighborhood café';
  $('afternoon-desc').textContent = 'Bring a book. Keep the afternoon flexible and indoors.';
  $('activity-price').textContent = '$18';
  updateBudget();
});'''
    tail = files['everyday/tools/app.js'].splitlines()[-1]
    return ('*** Begin Patch\n*** Update File: everyday/tools/index.html\n@@\n-'
            + old + '\n+' + new
            + '\n*** Update File: everyday/tools/app.js\n@@\n ' + tail + '\n'
            + ''.join('+' + line + '\n' for line in js.strip().splitlines())
            + '*** End Patch')


async def run(url, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'raw').mkdir(exist_ok=True)
    evidence = {
        'classification': 'rehearsed capability demo; deterministic driver, not an autonomous LLM benchmark',
        'sample_data': True,
        'capture': {'viewer_width': WIDTH, 'viewer_height': HEIGHT, 'native_desktop': [1280, 800]},
        'tools': [], 'verified': [], 'chapters': [],
    }
    origin = time.monotonic()
    async with httpx.AsyncClient(base_url=url, timeout=90) as http:
        csrf, token = await authenticate(http, url)
        async with streamablehttp_client(url + '/mcp', headers={'Authorization':'Bearer ' + token}) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()

                async def call(name, args=None, allow_error=False):
                    result = await client.call_tool(name, args or {})
                    assert allow_error or not result.isError, (name, str(result)[:2000])
                    evidence['tools'].append({'name':name,'time':round(time.monotonic()-origin,3),
                                              'success':not bool(result.isError)})
                    (out / 'evidence.json').write_text(json.dumps(evidence, indent=2))
                    return result

                async def coding(name, args, allow_error=False):
                    return await call(name, {**args, 'bridge_action_id':'demo-' + str(uuid.uuid4())}, allow_error=allow_error)

                async def browser(action):
                    observation = unpack(await call('browser_snapshot'))['observation_id']
                    return await call('browser_action', {'action':action,
                        'observation_id':observation,'action_id':'demo-' + str(uuid.uuid4())})

                async def desktop(action):
                    observation = unpack(await call('desktop_screenshot'))['observation_id']
                    return await call('desktop_action', {'action':action,
                        'observation_id':observation,'action_id':'demo-' + str(uuid.uuid4())})

                async def snapshot_contains(*texts):
                    for _ in range(20):
                        result = unpack(await call('browser_snapshot'))
                        if all(text in result['snapshot'] for text in texts):
                            return result
                        await asyncio.sleep(.3)
                    raise AssertionError((texts, result))

                async def click(name):
                    return await browser({'kind':'click','role':'button','name':name})

                async def capture(page, name):
                    # Wait for VNC to catch up with a completed actual tool action.
                    await asyncio.sleep(.8)
                    shot = await call('desktop_screenshot')
                    image = next(content for content in shot.content if content.type == 'image')
                    (out / 'raw' / f'{name}-desktop.png').write_bytes(base64.b64decode(image.data))
                    await page.screenshot(path=str(out / f'{name}.png'))
                    await page.locator('#screen canvas').screenshot(
                        path=str(out / f'{name}-desktop-view.png'))
                    (out / f'{name}-snapshot.json').write_text(
                        json.dumps(unpack(await call('browser_snapshot')), indent=2))

                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert {'coding_apply_patch','coding_exec_command','coding_read_file',
                        'browser_action','desktop_action'} <= tools
                await call('session_start')
                files = initial_files()
                await coding('coding_apply_patch', {'patch':add_files_patch(files)})
                read = await coding('coding_read_file', {'path':'everyday/tools/index.html'})
                assert 'Everyday Studio' in str(read)
                evidence['verified'].append('Coding Tools MCP created eight real workspace files and verified the app source')
                result = await coding('coding_exec_command', {
                    'cmd':'python everyday/scripts/analyze.py', 'yield_time_ms':1000})
                assert '126.40' in str(result), result
                summary = await coding('coding_read_file', {'path':'everyday/outputs/spending-summary.json'})
                assert '126.4' in str(summary), summary
                evidence['verified'].append('Coding Tools MCP executed Python over the sample CSV; five transactions total $126.40')
                async def server_status(server, label):
                    status = await coding('coding_write_stdin', {
                        'command_id':server.structuredContent['command_id'],
                        'chars':'', 'yield_time_ms':0})
                    (out / f'{label}-process.json').write_text(
                        json.dumps(status.model_dump(mode='json'), indent=2))
                    return status

                async def probe_server(label):
                    result = await coding('coding_exec_command', {
                        'cmd': 'curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8765/tools/index.html',
                        'yield_time_ms':5000, 'timeout_ms':6000, 'max_output_bytes':10000}, allow_error=True)
                    (out / f'{label}-probe.json').write_text(
                        json.dumps(result.model_dump(mode='json'), indent=2))
                    info = result.structuredContent or {}
                    if result.isError and (info.get('error') or {}).get('code') == 'PERMISSION_REQUIRED':
                        raise RuntimeError('Readiness probe requires permission; see diagnostic artifact')
                    return info.get('exit_code') == 0 and 'Everyday Studio' in str(result)

                # Preserve diagnostic evidence from the standard server first. If its
                # request handling fails, retry with the constrained demo-only server;
                # do not change filesystem permissions or bypass tool-policy denials.
                server = await coding('coding_exec_command', {
                    'cmd':'python -u -m http.server 8765 --bind 127.0.0.1 --directory everyday',
                    'yield_time_ms':500, 'timeout_ms':300000})
                assert server.structuredContent and server.structuredContent['status'] == 'running', server
                ready = await probe_server('standard-server')
                await server_status(server, 'standard-server')
                if not ready:
                    killed = await coding('coding_kill_command', {
                        'command_id':server.structuredContent['command_id'],
                        'signal':'TERM', 'wait_ms':1000, 'kill_wait_ms':1000})
                    assert (killed.structuredContent or {}).get('status') in {'killed','terminated','exited'}, killed
                    server = await coding('coding_exec_command', {
                        'cmd':'python -u everyday/scripts/serve.py',
                        'yield_time_ms':500, 'timeout_ms':300000})
                    assert server.structuredContent and server.structuredContent['status'] == 'running', server
                    for attempt in range(12):
                        ready = await probe_server(f'demo-server-{attempt}')
                        status = await server_status(server, f'demo-server-{attempt}')
                        if ready:
                            break
                        assert (status.structuredContent or {}).get('status') == 'running', status
                        await asyncio.sleep(.5)
                assert ready, 'Sample HTTP server did not become ready; see server process/probe artifacts'
                evidence['verified'].append('An actual Coding Tools HTTP probe verified the generated page before browser navigation')
                await browser({'kind':'navigate','url':'http://127.0.0.1:8765/tools/index.html'})
                await snapshot_contains('Make room for','Coding Tools MCP','$50')
                # F11 makes the existing headed Chromium fill the real 1280x800 desktop.
                await desktop({'kind':'key','keys':['F11']})
                await asyncio.sleep(1)
                evidence['verified'].append('Real headed Chromium displays the generated personal workspace')

                async with async_playwright() as pw:
                    observer = await pw.chromium.launch(args=[f'--window-size={WIDTH},{HEIGHT}'])
                    # HTTP login happened before recording; no tokens, cookies, or OAuth codes appear in shots.
                    cookies = [{'name':cookie.name, 'value':cookie.value, 'url':url}
                               for cookie in http.cookies.jar]
                    context = await observer.new_context(viewport={'width':WIDTH,'height':HEIGHT},
                        screen={'width':WIDTH,'height':HEIGHT}, device_scale_factor=1,
                        record_video_dir=str(out / 'video'), record_video_size={'width':WIDTH,'height':HEIGHT})
                    await context.add_cookies(cookies)
                    page = await context.new_page()
                    recording_start = time.monotonic()
                    video = page.video
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(url)
                    await page.locator('#screen[data-connected="true"]').wait_for(timeout=30000)
                    await page.wait_for_function('''() => {
                        const c=document.querySelector('#screen canvas');
                        if (!c || c.width!==1280 || c.height!==800) return false;
                        const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                        const colors=new Set();
                        for(let i=0;i<d.length;i+=400) colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);
                        return colors.size>30;
                    }''', timeout=30000)
                    await page.get_by_role('button',name='Refresh',exact=True).click()
                    await page.get_by_role('link',name='everyday/plans/packing-checklist.md',exact=True).wait_for()
                    await page.screenshot(path=str(out / '00-real-workspace-viewer.png'), full_page=True)
                    # Keep the observer in its ordinary viewport. Headless fullscreen
                    # can record only the backing window's 800x600 area, even while
                    # Playwright screenshots show a complete emulated viewport.
                    bounds = await page.locator('#screen canvas').bounding_box()
                    assert bounds and bounds['width'] > 800 and bounds['height'] > 500, bounds
                    assert bounds['x'] >= 0 and bounds['y'] >= 0, bounds
                    assert bounds['x'] + bounds['width'] <= WIDTH, bounds
                    assert bounds['y'] + bounds['height'] <= HEIGHT, bounds
                    evidence['capture']['video_crop'] = bounds
                    evidence['capture']['outer_fullscreen'] = False

                    async def chapter(title):
                        evidence['chapters'].append({'title':title,
                            'video_start_seconds':round(time.monotonic()-recording_start,2)})

                    await chapter('Interactive weekend planner')
                    await capture(page,'01-weekend-planner')
                    await asyncio.sleep(3)
                    await browser({'kind':'fill','role':'spinbutton','name':'Weekend budget','text':'450'})
                    await snapshot_contains('$80')
                    await click('Choose a free afternoon')
                    await snapshot_contains('Follow the coastal path','$110')
                    await capture(page,'02-budget-adjusted')
                    await asyncio.sleep(3)
                    evidence['verified'].append('Browser tools adjusted the budget to $450 and selected a free afternoon; remaining budget is $110')

                    await chapter('Coding Tools adds a rainy-day option')
                    await coding('coding_apply_patch', {'patch':rainy_day_patch(files)})
                    await browser({'kind':'navigate','url':'http://127.0.0.1:8765/tools/index.html?revision=rainy-day'})
                    await snapshot_contains('Plan a rainy afternoon')
                    await click('Plan a rainy afternoon')
                    await snapshot_contains('Find a cozy neighborhood café','$92')
                    await capture(page,'03-rainy-day-improvement')
                    await asyncio.sleep(3)
                    evidence['verified'].append('A second real Coding Tools patch added a rainy-day feature; the browser loaded and exercised it with $92 remaining')

                    await chapter('CSV becomes a spending snapshot')
                    await click('Spending snapshot')
                    await snapshot_contains('$126.40','$72.40','Calculated with Coding Tools MCP')
                    await capture(page,'04-spending-snapshot')
                    await asyncio.sleep(5)
                    evidence['verified'].append('The real browser displayed the generated JSON analysis and all five sample transactions')

                    await chapter('Desktop typing and an organized checklist')
                    await click('Packing checklist')
                    await browser({'kind':'click','role':'checkbox','name':'Walking shoes'})
                    await browser({'kind':'click','role':'checkbox','name':'Water bottle'})
                    await browser({'kind':'click','role':'textbox','name':'Packing note'})
                    await desktop({'kind':'type','text':'Bring the camera'})
                    await snapshot_contains('Bring the camera')
                    await click('Add item')
                    await snapshot_contains('2 / 6 ready','Bring the camera','Saved in this browser.')
                    await capture(page,'05-packing-checklist')
                    await asyncio.sleep(4)
                    evidence['verified'].append('Real desktop keyboard input added Bring the camera; browser tools checked two checklist items')
                    await coding('coding_apply_patch', {'patch':
                        '*** Begin Patch\n*** Update File: everyday/plans/packing-checklist.md\n@@\n'
                        '-- [ ] Walking shoes\n-- [ ] Water bottle\n'
                        '+- [x] Walking shoes\n+- [x] Water bottle\n@@\n - [ ] Light jacket\n'
                        '+- [ ] Bring the camera\n*** End Patch'})
                    # Confirm the saved file itself, not just browser storage or a screenshot.
                    downloaded = await http.get('/api/artifacts/everyday/plans/packing-checklist.md')
                    downloaded.raise_for_status()
                    assert '[x] Walking shoes' in downloaded.text and 'Bring the camera' in downloaded.text
                    evidence['verified'].append('The updated checklist was saved through Coding Tools MCP and downloaded from the real artifact API')
                    await chapter('Real exported files')
                    await page.get_by_role('button',name='Refresh',exact=True).click()
                    await page.get_by_role('link',name='everyday/outputs/spending-summary.json',exact=True).wait_for()
                    await page.screenshot(path=str(out / '06-files-and-desktop.png'),full_page=True)
                    await asyncio.sleep(3)
                    assert not errors, errors
                    await context.close()
                    saved_video = await video.path()
                    shutil.copyfile(saved_video, out / 'personal-capability-demo.webm')
                    await observer.close()
                    # Validate real encoded frames, not just page screenshots. The
                    # bottom-right quarter of the desktop must contain image detail;
                    # the earlier compositor defect produced a uniform gray matte.
                    evidence['capture']['verified_video_frames'] = []
                    for index, item in enumerate(evidence['chapters']):
                        at = item['video_start_seconds'] + 2
                        frame = out / f'video-check-{index:02d}.png'
                        proc = await asyncio.create_subprocess_exec(
                            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                            '-ss', str(at), '-i', str(out / 'personal-capability-demo.webm'),
                            '-frames:v', '1', str(frame),
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        _, stderr = await proc.communicate()
                        assert proc.returncode == 0, stderr.decode(errors='replace')
                        with Image.open(frame) as rendered:
                            assert rendered.size == (WIDTH, HEIGHT), rendered.size
                            x, y, width, height = (bounds[key] for key in ('x','y','width','height'))
                            detail = rendered.convert('RGB').crop((
                                int(x + width * .72), int(y + height * .72),
                                int(x + width * .97), int(y + height * .97)))
                            colors = detail.getcolors(maxcolors=1000000) or []
                            assert len(colors) > 16, (
                                'Encoded video is missing the lower-right desktop region', frame)
                        evidence['capture']['verified_video_frames'].append({
                            'file':frame.name, 'at_seconds':round(at,2), 'chapter':item['title']})
                    evidence['verified'].append(
                        'Encoded video frames at every chapter retain visible detail in the lower-right desktop region')
                exports = out / 'exports'
                for path in ['everyday/tools/index.html','everyday/tools/style.css','everyday/tools/app.js',
                             'everyday/data/sample-spending.csv','everyday/scripts/analyze.py','everyday/scripts/serve.py',
                             'everyday/outputs/spending-summary.json','everyday/outputs/spending-summary.md',
                             'everyday/plans/weekend-plan.md','everyday/plans/packing-checklist.md']:
                    response = await http.get('/api/artifacts/' + path)
                    response.raise_for_status()
                    target = exports / path
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(response.content)
                await http.post('/api/control/paused',headers={'X-CSRF-Token':csrf,'Origin':url})
                evidence['verified'].append('All ten generated files were exported; managed sample web server stopped after capture')
    evidence['complete'] = True
    (out / 'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'complete':True,'output':str(out),'verified':evidence['verified']},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--out',type=Path,default=Path('artifacts/personal-demo'))
    args = parser.parse_args()
    asyncio.run(run(args.url.rstrip('/'),args.out))
