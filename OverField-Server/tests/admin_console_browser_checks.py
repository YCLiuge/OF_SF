"""Drive the real owner console DOM; the caller supplies its isolated browser and API."""
def verify(js, until, get, post):
    def open_form(name):
        js("document.querySelector('nav [title=\""+name+"\"]').click()")
        until(lambda: js("document.querySelector('#route').textContent==='"+name+"' && !!document.querySelector('#editor-feedback')"))
    def select_item(value):
        js("var l=document.querySelector('.catalog-list');l.value='"+str(value)+"';l.dispatchEvent(new Event('change'))")
        assert js("document.querySelector('.catalog-list').value")==str(value)
    def mode(value):
        js("var m=document.querySelector('[data-control=mode]');m.value='"+value+"';m.dispatchEvent(new Event('change'))")
    def change(selector,value,event="input"):
        import json
        js(f"var f=document.querySelector({json.dumps(selector)});f.value={json.dumps(value)};f.dispatchEvent(new Event({json.dumps(event)}))")
    def save(expected=True):
        js("document.querySelector('#operation').requestSubmit()")
        if expected:
            until(lambda: js("document.querySelector('#editor-feedback')?.textContent.includes('已从区服重新读取')"))
        else:
            until(lambda: js("var f=document.querySelector('#editor-feedback');f && !!f.textContent && !f.textContent.includes('未保存') && !document.querySelector('#execute').disabled"))
    js("window.confirm=()=>true")
    verify_mail(js,until,get)
    verify_code(js,until,get)
    open_form('events.set')
    select_item(4008);mode('on')
    select_item(3001);mode('on')
    change('.editor-detail label:last-child select','true','change')
    save()
    rows={r['id']:r for r in get('events.get')['events']}
    assert rows[4008]['enabled'] and rows[3001]['enabled'] and rows[3001]['staged_gifts']
    select_item(4008);mode('off');save()
    assert {r['id']:r for r in get('events.get')['events']}[4008]['enabled'] is False
    select_item(4008);mode('stock');save()
    assert [e['id'] for e in get('events.get')['events']]==[3001]
    # A different operator saves while our page is still open.
    select_item(4008);mode('on')
    post('events.set',dict(events=[]));save(False)
    assert js("document.querySelector('#editor-feedback').textContent.includes('其他操作者')")
    assert not get('events.get')['events']
    js("document.querySelector('#fields>.quiet').click()")
    until(lambda:js("document.querySelector('#execute').disabled && document.querySelector('#editor-feedback')?.textContent===''"))
    open_form('plaza.set')
    change('#fields label select','custom','change')
    change('#fields input[type=datetime-local]','2026-11-25T05:00:00')
    change('#fields .editor-grid label:nth-child(2) input','2027-02-25T05:00:00')
    js("document.querySelector('.entrances input[value=\"5075\"]').click();document.querySelector('.entrances input[value=\"260815130\"]').click()")
    save(False)
    assert get('plaza.get')=={}
    js("document.querySelector('.entrances input[value=\"5075\"]').click()")
    save();assert get('plaza.get')['activities']==[260815130]
    open_form('blessings.set')
    tree=js("Number(document.querySelector('.catalog-list').value)")
    mode('on');change('.editor-grid input','2026-11-25T05:00:00')
    change('.editor-grid label:nth-child(2) input','2027-11-25T05:00:00')
    save();assert get('blessings.get')['trees']==[dict(id=tree,enabled=True,opens_at='2026-11-25 05:00:00',closes_at='2027-11-25 05:00:00')]
    open_form('gacha.set')
    def choose_pool(name, pool_id):
        change('.gacha-tools input[type=search]',name)
        js(f"document.querySelector('.gacha-card[data-pool=\"{pool_id}\"]').click()")
    choose_pool('莫莉',3023)
    assert js("document.querySelector('.gacha-detail').textContent.includes('雾都侦探')")
    until(lambda:js("document.querySelector('.gacha-card[data-pool=\"3023\"] img').naturalWidth>0"))
    choose_pool('常驻招募',2000)
    change('.gacha-detail .editor-grid label:first-child input','71')
    save();assert next(row for row in get('gacha.get')['pools'] if row['id']==2000)['pity']==71
    n=len(get('gacha.get')['pools'])
    choose_pool('宁蒙',3010); mode('off');save()
    assert not next(row for row in get('gacha.get')['pools'] if row['id']==3010)['enabled']
    open_form('gacha.enable')
    choose_pool('灰蓝偶像',3010)
    assert not js("document.querySelector('#execute').disabled")
    save()
    assert len(get('gacha.get')['pools'])==n and next(row for row in get('gacha.get')['pools'] if row['id']==3010)['enabled']

    open_form('rules.schedule')
    change('#fields .editor-grid label:first-child input','345')
    save();assert get('rules.get')['home_furniture_limit']==345
    js("document.querySelector('realm-overview>button').click()")
    until(lambda:js("document.querySelector('#route').textContent==='capacity.set'"))
    change('#field-limit','137');js("document.querySelector('#operation').requestSubmit()")
    until(lambda:js("document.querySelector('.metrics div:last-child b').textContent.endsWith('/ 137')"))
    assert get('capacity.get')['limit']==137

def verify_mail(js,until,get):
    def change(selector,value,event="input"):
        import json
        js(f"var f=document.querySelector({json.dumps(selector)});f.value={json.dumps(value)};f.dispatchEvent(new Event({json.dumps(event)}))")
    # Send from the actual operator form. The submitted draft cannot be sent twice.
    js("document.querySelector('nav [title=\"mail.send\"]').click()")
    change('[name=scope]','existing','change')
    change('[name=title]','隔离浏览器投递')
    change('[name=body]','管理台附件测试')
    change('.reward-filters input','金币')
    until(lambda:js("!!document.querySelector('[data-item=\"101\"]')"))
    js("document.querySelector('[data-item=\"101\"]').click()")
    change('[data-count="101"]','7')
    change('.reward-filters input','能量饮料')
    until(lambda:js("document.querySelector('.reward-cards').textContent.includes('能量饮料')"))
    assert js("JSON.parse(document.querySelector('[name=rewards]').value)[0].quantity===7")
    key=js("document.querySelector('[name=request_id]').value")
    js("document.querySelector('#operation').requestSubmit()")
    until(lambda:js("document.querySelector('#result').textContent.includes('已登记批次')"))
    js("document.querySelector('#operation').requestSubmit()")
    assert js("document.querySelector('#execute').disabled")
    rows=[r for r in get('mail.list')['batches'] if r['request_id']==key]
    assert len(rows)==1 and rows[0]['title']=='隔离浏览器投递' and rows[0]['rewards']==[{'item':101,'quantity':7}]
    js("document.querySelector('#result button').click()")
    assert js("document.querySelector('[name=request_id]').value")!=key
    js("""document.querySelector('nav [title="mail.list"]').click();document.querySelector('#operation').requestSubmit()""")
    until(lambda:js("document.querySelector('.reward-record')?.textContent.includes('金币 × 7')"))


def verify_code(js,until,get):
    js("""document.querySelector('nav [title="codes.issue"]').click();document.querySelector('[name=code]').value='BROWSER8'""")
    js("document.querySelector('#operation').requestSubmit()")
    assert js("document.querySelector('#status').textContent.includes('至少一种奖励')")
    js("var q=document.querySelector('.reward-filters input');q.value='星石';q.dispatchEvent(new Event('input'))")
    until(lambda:js("""!!document.querySelector('[data-item="102"]')"""))
    js("""document.querySelector('[data-item="102"]').click();var n=document.querySelector('[data-count="102"]');n.value=9;n.dispatchEvent(new Event('input'));document.querySelector('#operation').requestSubmit()""")
    until(lambda:js("document.querySelector('#result').textContent.includes('已创建兑换码 BROWSER8')"))
    assert js("document.querySelector('#execute').disabled")
    assert next(row for row in get('codes.list')['codes'] if row['code']=='BROWSER8')['rewards']=={'102':9}
    js("""document.querySelector('nav [title="codes.list"]').click();document.querySelector('#operation').requestSubmit()""")
    until(lambda:js("document.querySelector('.reward-record')?.textContent.includes('星石 × 9')"))


def verify_password_reset(js,until,player):
    import json
    user="console-reset";old="temporary-original";new="temporary-new-password"
    assert player("register",dict(username=user,password=old,nickname="口令回归"))[0]==201
    js("document.querySelector('nav [title=\"accounts.find\"]').click()")
    js("document.querySelector('#field-value').value="+json.dumps(user)+";document.querySelector('#operation').requestSubmit()")
    until(lambda:js("!!document.querySelector('#result button')"))
    assert js("document.querySelector('#result button').textContent").startswith(user)
    js("document.querySelector('#result button').click()")
    assert js("document.querySelector('#new_password').type==='password' && document.querySelector('#confirm-password').type==='password'")
    js("document.querySelector('#new_password').value="+json.dumps(new)+";document.querySelector('#confirm-password').value='different-test-password';document.querySelector('#operation').requestSubmit()")
    until(lambda:js("document.querySelector('#status').textContent.includes('不一致')"))
    js("document.querySelector('#confirm-password').value="+json.dumps(new)+";document.querySelector('#operation').requestSubmit()")
    until(lambda:js("document.querySelector('#status').textContent.includes('密码已重置')"))
    assert js("!document.querySelector('#new_password') && !document.querySelector('main').textContent.includes("+json.dumps(new)+")")
    assert player("login",dict(username=user,password=old))[0]==401
    assert player("login",dict(username=user,password=new))[0]==200


def verify_directory(js, until):
    """Visit the whole navigation without submitting mutations; inspect actual rendered results."""
    import json
    routes = js("Array.from(document.querySelectorAll('nav button[title]'),b=>b.title)")
    readers = {'realm.status','capacity.get','rules.get','plaza.get','events.get','blessings.get',
               'reports.list','photos.list','maintenance.list','codes.list','mail.list','rewards.catalog',
               'events.catalog','blessings.catalog','plaza.catalog'}
    for route in routes:
        key = json.dumps(route)
        js(f"document.querySelector('nav [title=' + CSS.escape({key}) + ']').click()")
        until(lambda:js(f"document.getElementById('route').textContent==={key}"))
        if route in readers:
            until(lambda:js("!document.getElementById('output').hidden && document.getElementById('result').textContent.trim().length>0 && !document.getElementById('execute').disabled"))
        if route.startswith('gacha.'):
            try:
                until(lambda:js("!!document.querySelector('#fields .gacha-card')"))
            except TimeoutError as error:
                raise AssertionError((route, js("document.getElementById('fields').innerText.slice(0,250)"))) from error
            js("document.querySelector('.gacha-card img')?.scrollIntoView({block:'center'})")
            until(lambda:js("Array.from(document.querySelectorAll('.gacha-card img')).some(img=>img.complete && img.naturalWidth>0)"))
        assert not js("Array.from(document.querySelectorAll('#fields textarea')).some(e=>e.offsetWidth && ['[]','{}'].includes(e.placeholder))"),route
        assert not js(r"document.querySelector('article pre:not([hidden])') || /^\s*[\[{]/.test(document.getElementById('result').innerText)"),route
    print(f'PASS {len(routes)} owner menu operations visited; read pages render without JSON.')
def verify_record_workflows(js, until, get, post, state):
    """Exercise record selection, older pages and 64-bit share codes in a disposable realm."""
    import json
    import sqlite3
    from contextlib import closing

    def navigate(name):
        js(f"document.querySelector('nav [title={json.dumps(name)}]').click()")
        until(lambda: js(f"document.querySelector('#route').textContent==={json.dumps(name)}"))
    def click(label, scope='#result'):
        expression=f"Array.from(document.querySelectorAll({json.dumps(scope+' button')})).find(b=>b.textContent==={json.dumps(label)} && !b.hidden && !b.disabled)"
        until(lambda: js(f"!!({expression}) && !document.getElementById('execute').disabled"))
        js(f"({expression}).click()")
    def text(value, selector='#result'):
        until(lambda: js(f"document.querySelector({json.dumps(selector)})?.textContent.includes({json.dumps(value)})"))
    def submit():
        js("document.querySelector('#operation').requestSubmit()")

    js("window.confirm=()=>true")
    post("notice.publish", {"title":"公告选择验收","body":"原始正文"})
    with closing(sqlite3.connect(state/'identity.sqlite3')) as db, db:
        db.executemany("INSERT INTO realm_notices(title,content,published,active) VALUES(?,'后续公告正文',1700000000000,1)",
                       ((f"后续公告{i:02}",) for i in range(50)))
    from local_identity import LocalIdentity
    from realm_maintenance import Maintenance
    native = Maintenance(LocalIdentity(state/'identity.sqlite3')).feed()['notices']
    assert len(native)==12 and native[0]['title']=='后续公告49' and native[-1]['title']=='后续公告38'
    first_page=get('maintenance.list')['notices']
    assert len(first_page)==30 and first_page[0]['title']=='后续公告49'
    older='maintenance.list?before='+str(first_page[-1]['id'])
    navigate("maintenance.list")
    click("查看更早记录")
    text("后续公告19")
    click("显示更多")
    text("公告选择验收")
    js("Array.from(document.querySelectorAll('#result .record-card')).find(c=>c.querySelector('h3').textContent==='公告选择验收').querySelector('button').click()")
    assert js("document.querySelector('#field-body').value")== "原始正文"
    js("document.querySelector('#field-body').value='已编辑正文'")
    submit()
    text("公告修改已保存")
    assert any(row['content']=='已编辑正文' for row in get(older)['notices'])
    navigate("notice.withdraw")
    click("读取更多记录","#fields")
    until(lambda:js("Array.from(document.querySelector('#field-id').options).some(o=>o.text.includes('公告选择验收'))"))
    js("var s=document.querySelector('#field-id');s.value=Array.from(s.options).find(o=>o.text.includes('公告选择验收')).value;s.dispatchEvent(new Event('change'))")
    submit()
    text("公告已撤回")
    assert all(row['title']!='公告选择验收' for row in get(older)['notices'])

    low=2**53+101
    with closing(sqlite3.connect(state/'game.sqlite', timeout=4)) as db,db:
        db.executemany("INSERT INTO realm_photos(code,owner,activity,posted,name,title,tag1,tag2,tag3,snapshot,fingerprint) VALUES(?,1,1,1700000000000,'摄影者',?,'','','',x'',?)",
                       ((low+i,f'照片测试{i:02}',str(i).encode()) for i in range(51)))
        db.execute("INSERT INTO players(player_id,player_name) VALUES(82411,'原角色名'),(82412,'被举报名')")
        db.execute("INSERT INTO realm_public_uids(player_id,uid) VALUES(82411,600000001),(82412,600000002)")
        db.execute("INSERT INTO realm_reports(reporter,target,category,channel,content,reason,photo,created) VALUES(600000001,600000002,8,0,'','图片内容',?,1700000000)",(low,))
    navigate("photos.list")
    text("照片测试50")
    click("查看更早记录")
    text("照片测试20")
    click("返回最新记录")
    text("照片测试50")
    navigate("photos.withdraw")
    click("读取更多记录","#fields")
    until(lambda:js(f"Array.from(document.querySelector('#field-code').options).some(o=>o.value==={json.dumps(str(low))})"))
    js(f"var s=document.querySelector('#field-code');s.value={json.dumps(str(low))};s.dispatchEvent(new Event('change'))")
    submit()
    text("照片已从公开照片墙撤回")
    remaining=get(f'photos.list?before={low+2}&limit=2')['photos']
    assert [r['code'] for r in remaining]==[low+1]
    navigate("reports.list")
    text("600000001")
    for label in ("原角色名","被举报名","分享照片",str(low)):
        assert js(f"document.querySelector('#result').textContent.includes({json.dumps(label)})")
    click("处理举报")
    text("图片内容","#field-id")
    assert js("!!document.querySelector('#field-id').value")
    submit()
    text("已将这条举报标记为已处理")
    assert get('reports.list?resolved=true')['reports'][0]['photo_text']==str(low)
    print('PASS notice edit/withdraw, photo paging and exact 64-bit withdrawal, translated report with player names and resolution.')
