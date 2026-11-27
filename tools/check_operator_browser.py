"""Exercise the self-hosted operator UI in a disposable Edge profile and realm."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import websocket

root = Path(__file__).resolve().parents[1]
server = root / 'OverField-Server'
if (root/'of.py').is_file():
    server = root
elif not server.is_dir():
    server = root / 'kit/OverField-Server'
sys.path.insert(0,str(server/'tests'))
from test_realm_admin_http import ports, request
profile, game, owner = ports()
token = secrets.token_urlsafe(40)
env = {k:v for k,v in os.environ.items() if not k.startswith('OF_')}
env.update(OF_ACCOUNT_REGISTRATION="open",OF_HTTP_PORT=str(profile),OF_GAME_PORT=str(game),OF_ADMIN_PORT=str(owner),OF_ADMIN_TOKEN=token)
browser = Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe'
def until(fn, seconds=35):
    limit=time.monotonic()+seconds
    while time.monotonic()<limit:
        try:
            value=fn()
            if value:return value
        except (OSError,ValueError): pass
        time.sleep(.12)
    raise TimeoutError('Timed out waiting for browser acceptance step')
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    base=Path(tmp)
    proc=subprocess.Popen([sys.executable,'-X','utf8','-B',server/'of.py','--state',base/'realm','start'],env=env,cwd=server,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    edge=None
    try:
        until(lambda:request(owner,'GET','/admin/v1/schema',token)[0]==200)
        profile_dir=base/'edge'
        edge=subprocess.Popen([browser,'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+str(profile_dir),'--window-size=1300,900',f'http://127.0.0.1:{owner}/admin/'],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        dev=until(lambda:(profile_dir/'DevToolsActivePort').read_text().splitlines()[0])
        def target():
            with urlopen(f'http://127.0.0.1:{dev}/json/list',timeout=2) as resp:
                return next((p['webSocketDebuggerUrl'] for p in json.load(resp) if p['url']==f'http://127.0.0.1:{owner}/admin/'),None)
        ws=websocket.create_connection(until(target),suppress_origin=True,timeout=10)
        seq=0
        def command(method,params={}):
            global seq
            seq+=1; ws.send(json.dumps(dict(id=seq,method=method,params=params)))
            while True:
                data=json.loads(ws.recv())
                if data.get('id')==seq:
                    if 'error' in data:raise RuntimeError(data['error']['message'])
                    return data['result']
        def js(expr):
            data=command('Runtime.evaluate',dict(expression=expr,returnByValue=True,awaitPromise=True))
            assert 'exceptionDetails' not in data, data.get('exceptionDetails',{}).get('text')
            return data['result'].get('value')
        until(lambda:js("!!customElements.get('realm-overview')"))
        js("document.getElementById('token').value="+json.dumps(token)+";document.getElementById('login').requestSubmit()")
        until(lambda:js("document.querySelector('.metrics b').textContent==='运行中'"))
        js("document.getElementById('filter').value='公告';document.getElementById('filter').dispatchEvent(new Event('input'));document.querySelector('nav [title=\"notice.publish\"]').click()")
        assert js("document.getElementById('field-body').tagName==='TEXTAREA' && !document.getElementById('execute').disabled")
        js("document.getElementById('field-title').value='区服维护';document.getElementById('field-body').value='测试活动已开放。';window.confirm=()=>true;document.getElementById('operation').requestSubmit()")
        until(lambda:js("!document.getElementById('output').hidden && JSON.parse(document.getElementById('result').textContent).id>0"))
        assert request(owner,'GET','/admin/v1/operations/maintenance.list',token)[1]['result']['notices'][0]['title']=='区服维护'
        js("document.getElementById('filter').value='';document.getElementById('filter').dispatchEvent(new Event('input'));document.querySelector('nav [title=\"notice.edit\"]').click()")
        assert js("document.querySelector('#clear-images').click();document.getElementById('field-images').disabled")
        from admin_console_browser_checks import verify
        def get(name):return request(owner,'GET','/admin/v1/operations/'+name,token)[1]['result']
        def post(name,data):return request(owner,'POST','/admin/v1/operations/'+name,token,data)[1]['result']
        verify(js,until,get,post)
        from admin_console_browser_checks import verify_password_reset
        verify_password_reset(js,until,lambda name,body:request(profile,"POST","/local/v1/accounts/"+name,data=body))

        command('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True))
        js("""document.querySelector('nav [title="codes.issue"]').click()""")
        until(lambda:js("!!document.querySelector('.reward-card')"))
        assert js("document.documentElement.scrollWidth<=document.documentElement.clientWidth")
        js("document.getElementById('logout').click()")
        assert js("document.getElementById('workspace').hidden && !document.getElementById('connect').hidden && document.getElementById('token').value===''")
        print('PASS Edge owner console: Chinese reward selection, mail and code histories, account reset, calendars, gifts, concurrent edits, capacity, notice, mobile layout and logout.')
        command('Browser.close')
    finally:
        if edge is not None and edge.poll() is None:
            try:edge.wait(6)
            except subprocess.TimeoutExpired:edge.terminate();edge.wait(10)
        if proc.poll() is None:
            try:request(owner,'POST','/admin/v1/operations/realm.stop',token,{})
            except OSError:pass
            try:proc.wait(8)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(10)
