"""Drive the real owner console DOM; the caller supplies its isolated browser and API."""
def verify(js, until, get, post):
    def open_form(name):
        js("document.querySelector('#quick [data-route=\""+name+"\"]').click()")
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
    select_item(2000);change('.editor-grid label:first-child input','71')
    save();assert next(row for row in get('gacha.get')['pools'] if row['id']==2000)['pity']==71
    n=len(get('gacha.get')['pools'])
    select_item(3010); mode('on');save()
    assert len(get('gacha.get')['pools'])==n+1
    open_form('rules.schedule')
    change('#fields .editor-grid label:first-child input','345')
    save();assert get('rules.get')['home_furniture_limit']==345
    js("document.querySelector('realm-overview>button').click()")
    until(lambda:js("document.querySelector('#route').textContent==='capacity.set'"))
    change('#field-limit','137');js("document.querySelector('#operation').requestSubmit()")
    until(lambda:js("document.querySelector('.metrics div:last-child b').textContent.endsWith('/ 137')"))
    assert get('capacity.get')['limit']==137

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
