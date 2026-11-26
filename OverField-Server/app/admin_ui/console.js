import {editorFor} from './editors.js';
const el = id => document.getElementById(id);
const groups = {
  accounts:'玩家账号',capacity:'连接容量', realm:'区服', maintenance:'运营概览', notice:'公告', release:'版本', events:'活动', plaza:'海上舞台',
  gacha:'招募', blessings:'祈愿树', rules:'玩法规则', codes:'兑换码', reports:'举报', photos:'照片墙', state:'备份'
};
const actions = {list:'列表', get:'查看配置', set:'修改配置', catalog:'内容目录', edit:'编辑', issue:'创建', publish:'发布',
  withdraw:'撤回', enable:'开启卡池', resolve:'标记已处理', backup:'备份存档', restore:'还原存档', schedule:'预设下次启动', status:'运行状态', stop:'正常停服'};
const labels = {username:'登录账号',account_ref:'账号标识',password_version:'密码版本',new_password:'新密码',field:'查询条件',value:'查询内容',id:'编号',file:'服务器上的文件路径',archive:'服务器上的备份路径',title:'标题',body:'正文',images:'服务器上的图片路径',
  version:'版本号',notes:'更新说明',sha256:'SHA-256',limit:'每页条数',before:'上一页最后一项编号',enabled:'启用',
  code:'兑换码或分享码',quota:'总名额',kind:'类型',rewards:'奖励',expires_at:'到期时间',trees:'祈愿树排期',pools:'卡池配置',
  resolved:'已处理',events:'活动配置',stages:'舞台配置'};
const lifecycle = {running:'运行中',stopping:'正在停服',stopped:'已停服',busy:'维护中',uninitialized:'尚未初始化',stale:'上次退出异常'};
let token = '', operations = {}, selected = '', session = 0, busy = false;
const forms = new Set(['events.set','plaza.set','blessings.set','gacha.set','rules.schedule']);
let editor = null, selectionVersion = 0;
const message = value => { el('status').textContent = value; };
function node(tag, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}
function displayName(name) {
  const names={'accounts.find':'查询玩家账号','accounts.reset':'重置玩家密码','events.set':'活动排期','plaza.set':'舞台与活动入口','blessings.set':'祈愿树排期','gacha.set':'招募池与概率','rules.schedule':'预设玩法参数','capacity.set':'调整连接上限'};
  if(names[name])return names[name];
  const [group, action] = name.split('.');
  return (groups[group] || group) + ' · ' + (actions[action] || action);
}
async function request(path, method = 'GET', payload, options = {}) {
  const headers = {Authorization: 'Bearer ' + token};
  if (options.tag) headers['If-Match'] = options.tag;
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, body: payload, credentials:'omit', cache:'no-store'});
  const body = await response.json();
  if (!response.ok || !body.ok) {
    const raw=body.error?.message;
    const friendly={
      'Cannot open before the native calendar':'活动开始时间不能早于原排期。',
      'Cannot close after the native calendar':'活动结束时间不能晚于原排期。',
      'Activity window is empty':'请检查活动的起止时间。',
      'Empty blessing tree window':'祈愿树结束时间须在开始时间之后。',
      'Plaza opening must precede closing':'海上舞台的结束时间须晚于开始时间。',
      'Choose one edition of the courtyard dialogue':'幽冥庭院的首期与复刻共用剧情，请选择其中一期。',
      'Choose one photo competition at a time':'同一舞台排期只能安排一场摄影比赛。',
      'Choose one battle pass season at a time':'同一舞台排期只能选择一期通行证。',
      'Reward weights must be integer basis points totaling 10000':'奖励组使用整数万分比，合计须为 10000。'
    };
    throw Error(friendly[raw] || raw || 'HTTP ' + response.status);
  }
  return options.snapshot ? {data:body.result,tag:response.headers.get('ETag')} : body.result;
}
class RealmOverview extends HTMLElement {
  connectedCallback() {
    const head = node('header'), refresh = node('button','刷新状态');
    refresh.className = 'quiet light'; refresh.type = 'button'; refresh.onclick = () => this.refresh();
    head.append(node('h2','区服概况'),refresh);
    this.values = []; const row = node('div'); row.className = 'metrics';
    for (const title of ['服务状态','注册账号','游戏角色','在线连接 / 上限']) {
      const cell = node('div'), value = node('b','—');
      cell.append(node('span', title), value); row.append(cell); this.values.push(value);
    }
    const edit = node('button','调整上限'); edit.type='button'; edit.className='quiet light';
    edit.onclick=()=>select('capacity.set'); this.capacityEdit=edit; edit.hidden=true;
    this.button = refresh; this.replaceChildren(head,row,edit);
  }
  reset() { this.values.forEach(field => field.textContent = '—'); this.button.disabled = false; }
  async refresh() {
    const epoch = session; this.button.disabled = true;
    try {
      const hasCapacity=Object.hasOwn(operations,'capacity.get');
      const [data,capacity]=await Promise.all([request('/admin/v1/operations/realm.status'),
        hasCapacity?request('/admin/v1/operations/capacity.get'):null]);
      this.capacityEdit.hidden=!Object.hasOwn(operations,'capacity.set');
      this.currentLimit=capacity?.limit;
      if (epoch !== session) return;
      [lifecycle[data.lifecycle] || data.lifecycle, data.accounts, data.characters, capacity?String(capacity.used ?? '—')+' / '+capacity.limit:'—'].forEach((value,index) => {
        this.values[index].textContent = value ?? '—';
      });
    } catch(error) { if (epoch === session) message(error.message); }
    finally { if (epoch === session) this.button.disabled = false; }
  }
}
customElements.define('realm-overview', RealmOverview);
const overview = document.querySelector('realm-overview');
function navigation() {
  const term = el('filter').value.trim().toLowerCase(), nav = el('operations');
  const expanded = new Set(Array.from(nav.querySelectorAll('details[open]'), group => group.dataset.key));
  const sections = new Map();
  for (const name of Object.keys(operations).sort()) {
    if (!(name + displayName(name) + operations[name].description).toLowerCase().includes(term)) continue;
    const key = name.split('.')[0]; let group = sections.get(key);
    if (!group) {
      group = node('details'); group.dataset.key = key;
      group.open = !!term || key === selected.split('.')[0] || expanded.has(key);
      group.append(node('summary', groups[key] || key)); sections.set(key, group);
    }
    const btn = node('button',displayName(name)); btn.type = 'button'; btn.title = name;
    btn.setAttribute('aria-current',String(name === selected));
    btn.onclick = () => select(name); group.append(btn);
  }
  nav.replaceChildren(...sections.values());
  if (!sections.size) nav.append(node('p','未找到匹配操作'));
}
function field(key, spec, required) {
  const type = [].concat(spec.type || 'string').find(t => t !== 'null');
  const wrap = node('div'), label = node('label',(labels[key] || key) + (required ? ' *' : ''));
  const lines = key === 'images' && type === 'array';
  let input;
  if (spec.enum || type === 'boolean') {
    input = node('select'); input.add(new Option('请选择',''));
    for (const value of spec.enum || [true,false]) input.add(new Option(value === true ? '是' : value === false ? '否' : ({username:'登录账号',nickname:'游戏名',uid:'公开 UID'})[value] || String(value),String(value)));
  } else if (lines || ['object','array'].includes(type) || ['body','notes'].includes(key)) {
    input = node('textarea'); input.rows = lines ? 3 : 5;
    if (lines) input.placeholder = '每行一个文件路径，最多 ' + spec.maxItems + ' 张';
    else if (type === 'array' || type === 'object') input.placeholder = type === 'array' ? '[]' : '{}';
  } else {
    input = node('input'); input.type = 'text';
    if (type === 'integer') {input.inputMode = 'numeric';input.pattern = '-?(0|[1-9][0-9]*)';}
  }
  input.name = key; input.id = 'field-' + key; input.required = required; input.dataset.type = lines ? 'lines' : type;
  label.htmlFor = input.id; wrap.append(label,input);
  if (lines && selected === 'notice.edit') {
    const clear = node('input'); clear.type = 'checkbox'; clear.id = 'clear-images';
    const toggle = node('label'); toggle.className = 'switch'; toggle.append(clear, document.createTextNode('移除原有图片'));
    clear.onchange = () => {input.disabled = clear.checked;}; wrap.append(toggle);
  }
  return wrap;
}
function select(name,force=false) {
  if (!force && editor?.dirty && !window.confirm('离开将放弃未保存的修改。继续？')) return;
  const version=++selectionVersion; editor=null;
  selected = name;
  for(const b of el('quick').children)b.setAttribute('aria-current',String(b.dataset.route===name));
  const op = operations[name], schema = op.inputSchema;
  el('title').textContent = displayName(name); el('route').textContent = name;
  el('description').textContent = op.availability === 'stopped' ? '请在正常停服后通过服主 CLI 执行此操作。' : (/[\u3400-\u9fff]/.test(op.description) ? op.description : '');
  el('mode').textContent = op.readOnly ? '只读' : op.availability === 'stopped' ? '停服操作' : '更改区服';
  el('fields').replaceChildren(...Object.entries(schema.properties || {}).map(([key,spec]) => field(key,spec,schema.required?.includes(key) || false)));
  el('schema').textContent = (op.readOnly ? 'GET ' : 'POST ') + '/admin/v1/operations/' + name + '\n\n' + JSON.stringify(schema,null,2);
  el('output').hidden = true; el('result').textContent = '';
  el('execute').textContent = op.readOnly ? '查询' : op.availability === 'stopped' ? '请通过 CLI 执行' : displayName(name);
  el('execute').disabled = busy || op.availability === 'stopped'; message(''); navigation();
  if(name==='accounts.reset'){
    const choice=el('fields'); choice.replaceChildren(node('p','请先查找账号，在结果中选择要重置密码的玩家。'));
    const find=node('button','查找账号');find.type='button';find.onclick=()=>select('accounts.find');choice.append(find);
    el('execute').hidden=true;
  } else el('execute').hidden=false;
  if(name==='accounts.find')el('field-field').value='username';
  if (forms.has(name)) openEditor(name,version);
  if (name==='capacity.set') {
    const limit=el('field-limit'); limit.type='number'; limit.min='1'; limit.max='512';limit.step='1';
    limit.labels[0].textContent='最大同时连接数（1–512）'; if(overview.currentLimit)limit.value=overview.currentLimit;
  }
}
async function openEditor(name,version) {
  const group=name.split('.')[0], fields=el('fields'), save=el('execute');
  fields.replaceChildren(node('p','正在读取区服配置…')); save.textContent='保存配置'; save.disabled=true;
  try {
    const route='/admin/v1/operations/'+group+'.';
    const [snapshot,catalog]=await Promise.all([request(route+'get','GET',undefined,{snapshot:true}),
      group==='rules'?null:request(route+'catalog')]);
    if (selectionVersion!==version) return;
    if (!snapshot.tag) throw Error('当前服务端未提供配置版本，请更新管理台服务。');
    const entry=editorFor(group,operations[name].inputSchema,catalog,snapshot.data,()=>{
      editor.dirty=true; save.disabled=false; el('editor-feedback').textContent='有未保存的修改';
    });
    editor={...entry,tag:snapshot.tag,dirty:false};
    const reload=node('button','重新读取区服配置');reload.type='button';reload.className='quiet light';
    reload.onclick=()=>select(name);
    const messageBox=node('p');messageBox.id='editor-feedback';messageBox.setAttribute('role','status');
    messageBox.setAttribute('aria-live','polite');fields.replaceChildren(entry.host,reload,messageBox);
  } catch(error) {
    if(selectionVersion!==version)return;
    const retry=node('button','重试');retry.type='button';retry.onclick=()=>select(name,true);
    fields.replaceChildren(node('p',error.message),retry);
  }
}
function chooseAccount(row){
  select('accounts.reset');
  const fields=el('fields');fields.replaceChildren(node('p',accountLabel(row)));
  for(const name of ['username','account_ref','password_version']){
    const hidden=node('input');hidden.type='hidden';hidden.name=name;hidden.dataset.type=name==='password_version'?'integer':'string';
    hidden.value=String(row[name]);fields.append(hidden);
  }
  for(const [id,title] of [['new_password','新密码'],['confirm-password','再次输入新密码']]){
    const input=node('input');input.type='password';input.autocomplete='new-password';input.required=true;input.id=id;
    if(id==='new_password'){input.name=id;input.dataset.type='string';}
    const label=node('label',title);label.htmlFor=id;fields.append(label,input);
  }
  el('execute').hidden=false;
}
function accountLabel(row){
  return row.username+' · '+(row.nickname||'尚未取名')+' · '+(row.uid?'UID '+row.uid:'尚未创建角色');
}
function accountResults(result){
  const host=el('result');host.replaceChildren();
  if(!result.accounts.length){host.textContent='没有找到匹配账号。';return;}
  for(const row of result.accounts){
    const select=node('button',accountLabel(row)+' · 重置密码');select.type='button';
    select.onclick=()=>chooseAccount(row);host.append(select,document.createTextNode('\n'));
  }
}
function collect() {
  const encoded = [], query = new URLSearchParams();
  for (const input of el('fields').querySelectorAll('[name]')) {
    const {name,value} = input, type = input.dataset.type;
    const clear = name === 'images' && el('clear-images')?.checked;
    if (value === '' && !clear) continue;
    let json;
    if (clear) json = '[]';
    else if (type === 'lines') json = JSON.stringify(value.split(/\r?\n/).map(v => v.trim()).filter(Boolean));
    else if (type === 'string') json = JSON.stringify(value);
    else {
      try { JSON.parse(value); } catch { throw Error((labels[name] || name) + '的输入格式有误'); }
      json = value;
    }
    encoded.push(JSON.stringify(name) + ':' + json);
    query.set(name, type === 'string' ? value : json);
  }
  return {body:'{' + encoded.join(',') + '}',query:query.toString()};
}
el('login').onsubmit = async event => {
  event.preventDefault(); const epoch = ++session; token = el('token').value; message('');
  try {
    const spec = await request('/admin/v1/schema');
    if (epoch !== session) return;
    operations = spec.operations; el('token').value = ''; el('connect').hidden = true;
    el('workspace').hidden = false; el('logout').hidden = false;
    const quick=el('quick');quick.replaceChildren();
    for (const key of forms) {
      if (!operations[key])continue;
      const btn=node('button',displayName(key));btn.type='button';btn.dataset.route=key;
      btn.onclick=()=>select(key);quick.append(btn);
    }
    select(operations['events.set']?'events.set':Object.keys(operations)[0]); overview.refresh();
  } catch(error) { if (epoch === session) {token = ''; message(error.message);} }
};
el('logout').onclick = () => {
  ++session; ++selectionVersion; editor=null; token = ''; operations = {}; selected = ''; busy = false;
  el('filter').value = ''; el('token').value = '';
  for (const id of ['schema','result','fields','operations']) el(id).replaceChildren();
  overview.reset(); el('workspace').hidden = true; el('logout').hidden = true;
  el('connect').hidden = false; el('token').focus(); message('');
};
el('filter').oninput = navigation;
el('operation').onsubmit = async event => {
  event.preventDefault(); const name = selected, epoch = session, op = operations[name];
  if (busy || !op || op.availability === 'stopped') return;
  const version=selectionVersion;
  if(forms.has(name)) {
    const target=editor;
    if(!target?.dirty)return;
    try {
      const body=target.values();
      if (name==='rules.schedule' && Object.keys(body).length===0) {
        target.dirty=false;el('execute').disabled=true;el('editor-feedback').textContent='没有待保存的修改';return;
      }
      busy=true;el('execute').disabled=true;el('fields').inert=true;
      const route='/admin/v1/operations/'+name;
      await request(route,'POST',JSON.stringify(body),{tag:target.tag});
      target.dirty=false;
      if(version!==selectionVersion)return;
      await openEditor(name,version);
      if(version===selectionVersion && el('editor-feedback'))el('editor-feedback').textContent='已保存，并已从区服重新读取。';
    } catch(error) {
      if(version===selectionVersion) el('editor-feedback').textContent=error.message;
    } finally {
      if(epoch===session)busy=false;
      el('fields').inert=false;
      if(version===selectionVersion)el('execute').disabled=!editor?.dirty;
    }
    return;
  }
  try {
    if(name==='accounts.reset'){
      if(!el('new_password'))return;
      const value=el('new_password').value;
      if([...value].length<8 || [...value].length>128)throw Error('请输入 8 至 128 个字符的新密码。');
      if(value!==el('confirm-password').value)throw Error('两次输入的新密码不一致。');
    }
    const {body,query} = collect();
    if (!op.readOnly && !window.confirm('确认执行「' + displayName(name) + '」？')) return;
    busy = true; el('execute').disabled = true; message('');
    const path = '/admin/v1/operations/' + name + (op.readOnly && query ? '?' + query : '');
    const result = await request(path, op.readOnly ? 'GET' : 'POST', op.readOnly ? undefined : body);
    if (name==='capacity.set' && epoch===session)await overview.refresh();
    if (epoch === session && selected === name) {el('output').hidden = false; if(name==='accounts.find')accountResults(result);
      else if(name==='accounts.reset'){select('accounts.find',true);message('密码已重置，玩家需使用新密码登录。');}
      else el('result').textContent = JSON.stringify(result,null,2);}
  } catch(error) { if (epoch === session) message(error.message); }
  finally { if (epoch === session) {busy = false; el('execute').disabled = operations[selected].availability === 'stopped' || (forms.has(selected) && !editor?.dirty);} }
};
