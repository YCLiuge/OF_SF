import {gachaPage} from './gacha.js';
import {resultsFor} from './results.js';
import {enhanceFields} from './form_fields.js';
import {editorFor} from './editors.js';
import {composeMail} from './mail_editor.js';
import {rewardPicker} from './reward_picker.js';
let mailForm=null, picker=null, draftDirty=false, delivered=false;
const categories = {
  '运行与维护':['realm','capacity','rules','state'],
  '玩家与社区':['accounts','reports','photos'],
  '邮件与奖励':['mail','codes','rewards'],
  '活动与舞台':['events','plaza'],
  '招募与祈愿':['gacha','blessings'],
  '公告与版本':['maintenance','notice','release']
};
const el = id => document.getElementById(id);
const groups = {
  mail:'游戏邮件',accounts:'玩家账号',capacity:'连接容量', realm:'区服', maintenance:'运营概览', notice:'公告', release:'版本', events:'活动', plaza:'海上舞台',
  gacha:'招募', blessings:'祈愿树', rules:'玩法规则', codes:'兑换码', reports:'举报', photos:'照片墙', state:'备份'
};
const actions = {list:'列表', get:'查看配置', set:'修改配置', catalog:'内容目录', edit:'编辑', issue:'创建', publish:'发布',
  withdraw:'撤回', enable:'开启卡池', resolve:'标记已处理', backup:'备份存档', restore:'还原存档', schedule:'预设下次启动', status:'运行状态', stop:'正常停服'};
const labels = {username:'登录账号',account_ref:'账号标识',password_version:'密码版本',new_password:'新密码',field:'查询条件',value:'查询内容',id:'编号',file:'服务器上的文件路径',archive:'服务器上的备份路径',title:'标题',body:'正文',images:'服务器上的图片路径',
  version:'版本号',notes:'更新说明',sha256:'SHA-256',limit:'每页条数',before:'上一页最后一项编号',enabled:'启用',
  code:'兑换码或分享码',quota:'总名额',kind:'类型',rewards:'奖励',expires_at:'到期时间',trees:'祈愿树排期',pools:'卡池配置',
  resolved:'已处理',events:'活动配置',stages:'舞台配置',channel:'发放方式',group:'物品分类',query:'名称或编号',offset:'跳过条数'};
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
  const names={'rewards.catalog':'查询奖励目录','codes.list':'查看兑换码','codes.issue':'创建兑换码','codes.withdraw':'停用兑换码','maintenance.list':'查看已发布内容','mail.send':'投递游戏邮件','mail.list':'查看投递记录','accounts.find':'查询玩家账号','accounts.reset':'重置玩家密码','events.set':'活动排期','plaza.set':'舞台与活动入口','blessings.set':'祈愿树排期','gacha.set':'招募池与概率','rules.schedule':'预设玩法参数','capacity.set':'调整连接上限'};
  if(names[name])return names[name];
  const [group, action] = name.split('.');
  return (groups[group] || group) + ' · ' + (actions[action] || action);
}
async function request(path, method = 'GET', payload, options = {}) {
  const epoch = session;
  const headers = {Authorization: 'Bearer ' + token};
  if (options.tag) headers['If-Match'] = options.tag;
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  const init = {method, headers, body: payload, credentials:'omit', cache:'no-store'};
  let response, body;
  for (const delay of [0,150,400,750]) {
    if (delay) await new Promise(resolve=>setTimeout(resolve,delay));
    if(epoch!==session) throw Error('管理会话已结束。');
    response = await fetch(path,init);
    body = await response.json();
    if(method!=='GET' || body.error?.code!=='state_busy') break;
  }
  if (!response.ok || !body.ok) {
    const raw=body.error?.message;
    const friendly={
      'Server state is in use. Stop the server and retry.':'区服正在处理另一项操作，请稍后重试。',
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
  const categoryOf = name=>Object.keys(categories).find(key=>categories[key].includes(name.split('.')[0])) || '其他操作';
  for (const name of Object.keys(operations).sort()) {
    if (name==='rules.catalog') continue;
    if (!(name + displayName(name) + categoryOf(name) + operations[name].description).toLowerCase().includes(term)) continue;
    const key = categoryOf(name); let group = sections.get(key);
    if (!group) {
      group = node('details'); group.dataset.key = key;
      group.open = !!term || key === categoryOf(selected) || expanded.has(key);
      group.append(node('summary', groups[key] || key)); sections.set(key, group);
    }
    const btn = node('button',displayName(name)); btn.type = 'button'; btn.title = name;
    btn.setAttribute('aria-current',String(name === selected));
    btn.onclick = () => select(name); group.append(btn);
  }
  nav.replaceChildren(...[...Object.keys(categories),'其他操作'].filter(key=>sections.has(key)).map(key=>sections.get(key)));
  if (!sections.size) nav.append(node('p','未找到匹配操作'));
}
function field(key, spec, required) {
  const type = [].concat(spec.type || 'string').find(t => t !== 'null');
  const wrap = node('div'), label = node('label',(labels[key] || key) + (required ? ' *' : ''));
  const lines = key === 'images' && type === 'array';
  let input;
  if (spec.enum || type === 'boolean') {
    input = node('select'); input.add(new Option('请选择',''));
    for (const value of spec.enum || [true,false]) input.add(new Option(value === true ? '是' : value === false ? '否' : ({username:'登录账号',nickname:'游戏名',uid:'公开 UID'})[value] || ({mail:'游戏邮件',codes:'兑换码',redemption:'普通兑换码',inherit:'继承奖励码'})[value] || String(value),String(value)));
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
  if (!force && (editor?.dirty || draftDirty) && !window.confirm('离开将放弃未保存的修改。继续？')) return;
  if (busy && !force) return;
  const version=++selectionVersion; editor=null; draftDirty=false; delivered=false; picker=null;
  document.querySelector('article').hidden=false;
  el('shortcuts').hidden=true;overview.hidden=true;
  selected = name;el('fields').inert=false;
  for(const b of el('quick').children)b.setAttribute('aria-current',String(b.dataset.route===name));
  const op = operations[name], schema = op.inputSchema;
  el('title').textContent = displayName(name); el('route').textContent = name;
  el('description').textContent = op.availability === 'stopped' ? '请在正常停服后通过服主 CLI 执行此操作。' : (/[\u3400-\u9fff]/.test(op.description) ? op.description : '');
  el('mode').textContent = op.readOnly ? '只读' : op.availability === 'stopped' ? '停服操作' : '更改区服';
  el('fields').replaceChildren(...Object.entries(schema.properties || {}).filter(([key])=>key!=='include_names').map(([key,spec]) => field(key,spec,schema.required?.includes(key) || false)));

  el('output').hidden = true; el('result').textContent = '';
  el('execute').textContent = op.readOnly ? '查询' : op.availability === 'stopped' ? '请通过 CLI 执行' : displayName(name);
  el('execute').disabled = busy || op.availability === 'stopped'; message(''); navigation();
  if(name==='accounts.reset'){
    const choice=el('fields'); choice.replaceChildren(node('p','请先查找账号，在结果中选择要重置密码的玩家。'));
    const find=node('button','查找账号');find.type='button';find.onclick=()=>select('accounts.find');choice.append(find);
    el('execute').hidden=true;
  } else el('execute').hidden=false;
  if(name==='accounts.find')el('field-field').value='username';
  mailForm=null;
  if(name==='mail.send' || name==='codes.issue'){
    picker=rewardPicker(name==='mail.send'?'mail':'codes', params=>request('/admin/v1/operations/rewards.catalog?'+params));
    if(name==='mail.send') mailForm=composeMail(el('fields'),picker);
    else {
      el('field-rewards').parentElement.replaceWith(picker.host);
      el('field-kind').value='redemption';
      const quota=el('field-quota'); quota.type='number'; quota.min='0'; quota.max='10000000'; quota.value='0';
      quota.labels[0].textContent='可领取人数（0 表示不限）';
      const expires=el('field-expires_at'); expires.type='datetime-local';expires.step='1';
      expires.labels[0].textContent='到期时间（北京时间，可留空）';
    }
  }
  if (name.startsWith('gacha.')) {openGacha(name,version);return;}
  if (forms.has(name)) openEditor(name,version);
  else if(op.availability==='stopped'){
    el('fields').replaceChildren(node('p', name==='state.restore'?'停止区服后，可在服务器的存档工具中选择备份并还原。':name==='state.backup'?'停止区服后，可在服务器的存档工具中创建备份。':'此配置需要离线修改。可在「预设玩法参数」提前安排支持的项目。'));
    el('execute').hidden=true;
  }else{
    if(name==='rewards.catalog')el('field-channel').value='mail';
    Promise.resolve(enhanceFields(name,el('fields'),request,mailForm)).catch(error=>{if(version===selectionVersion)message(error.message);});
    if(op.readOnly && (!schema.required?.length || name==='rewards.catalog')) queueMicrotask(()=>{
      if(version===selectionVersion)el('operation').requestSubmit();
    });
  }
  if (name==='capacity.set') {
    const limit=el('field-limit'); limit.type='number'; limit.min='1'; limit.max='512';limit.step='1';
    limit.labels[0].textContent='最大同时连接数（1–512）'; if(overview.currentLimit)limit.value=overview.currentLimit;
  }
}
async function openGacha(name,version,feedback='') {
  const mode=name.split('.')[1],save=el('execute'),fields=el('fields');
  fields.replaceChildren(node('p','正在读取卡池…'));
  save.hidden=mode==='catalog'||mode==='get';save.disabled=true;
  save.textContent=mode==='enable'?'开启选中的卡池':'保存配置';
  try {
    const [catalog,snapshot]=await Promise.all([request('/admin/v1/operations/gacha.catalog'),
      request('/admin/v1/operations/gacha.get','GET',undefined,{snapshot:true})]);
    if(version!==selectionVersion)return;
    const entry=gachaPage(mode,catalog,snapshot.data,(allowed=true)=>{if(editor){
        editor.dirty=allowed;
        const feedback=el('editor-feedback');
        if(feedback)feedback.textContent=allowed?'有未保存的修改':'';
      }
      save.disabled=!allowed;});
    const hint=node('p',feedback);hint.id='editor-feedback';hint.setAttribute('role','status');
    editor={...entry,tag:snapshot.tag,dirty:false};
    const reload=node('button','重新读取卡池');reload.type='button';reload.className='quiet light';reload.onclick=()=>select(name);
    fields.replaceChildren(entry.host,reload,hint);
  } catch(error) {
    if(version!==selectionVersion)return;
    const retry=node('button','重试');retry.type='button';retry.onclick=()=>select(name,true);
    fields.replaceChildren(node('p',error.message),retry);
  }
}
async function openEditor(name,version) {
  const group=name.split('.')[0], fields=el('fields'), save=el('execute');
  fields.replaceChildren(node('p','正在读取区服配置…')); save.textContent='保存配置'; save.disabled=true;
  try {
    const route='/admin/v1/operations/'+group+'.';
    const [snapshot,catalog]=await Promise.all([request(route+'get','GET',undefined,{snapshot:true}),
      request(route+'catalog')]);
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
function rewardHistory(name, result) {
  const host=el('result'), mail=name==='mail.list';
  const rows=mail ? result.batches : result.codes;
  host.replaceChildren();
  if (!rows.length) {host.textContent=mail?'还没有投递记录。':'还没有发行兑换码。';return;}
  const scopes={existing:'发送时已有的玩家',ongoing:'含有效期内加入的新玩家'};
  for (const row of rows) {
    const entry=node('section');entry.className='reward-record';
    entry.append(node('h3',mail?row.title:row.code));
    const info=mail ? (scopes[row.scope]||row.username)+' · 发送时 '+row.recipients_at_issue+' 人 · 批次 '+row.id
        : (row.enabled?'已启用':'已停用')+' · '+(row.quota?'限 '+row.quota+' 人':'不限名额')+(row.expires_at?' · 至 '+row.expires_at:'');
    entry.append(node('small',info));
    const rewards=mail ? row.rewards : Object.entries(row.rewards).map(([item,quantity])=>({item,quantity}));
    entry.append(node('p',rewards.map(({item,quantity})=>(result.item_names[item]||('物品 '+item))+' × '+quantity).join('、') || '无附件'));
    if (mail) {
      const detail=node('details');detail.append(node('summary','查看正文'),node('p',row.body));entry.append(detail);
    }
    host.append(entry);
  }
}
function collect() {
  const encoded = [], query = new URLSearchParams();
  if (selected==='mail.list' || selected==='codes.list') query.set('include_names','true');
  for (const input of el('fields').querySelectorAll('[name]')) {
    const {name,value} = input, type = input.dataset.type;
    const clear = name === 'images' && el('clear-images')?.checked;
    if (value === '' && !clear) continue;
    let json;
    if (clear) json = '[]';
    else if (type === 'lines') json = JSON.stringify(value.split(/\r?\n/).map(v => v.trim()).filter(Boolean));
    else if (name==='expires_at' && input.type==='datetime-local') json=JSON.stringify(value.replace('T',' ')+(value.length===16?':00':''));
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
el('fields').addEventListener('input', event=>{
  if (operations[selected]?.readOnly && event.target.name && !['before','offset'].includes(event.target.name))
    for(const key of ['before','offset']) {const field=el('field-'+key);if(field)field.value='';}
  if(picker && (event.target.name || event.target===picker.host))draftDirty=true;
});
function home(){
  if(busy || ((editor?.dirty || draftDirty) && !window.confirm('离开将放弃未保存的修改。继续？'))) return;
  selected='';editor=null;picker=null;mailForm=null;draftDirty=false;delivered=false;++selectionVersion;
  document.querySelector('article').hidden=true;el('fields').replaceChildren();
  el('shortcuts').hidden=false;overview.hidden=false;navigation();message('');
}
el('home').onclick=home;
el('login').onsubmit = async event => {
  event.preventDefault(); const epoch = ++session; token = el('token').value; message('');
  try {
    const spec = await request('/admin/v1/schema');
    if (epoch !== session) return;
    operations = spec.operations; el('token').value = ''; el('connect').hidden = true;
    el('workspace').hidden = false; el('logout').hidden = false;
    const quick=el('quick');quick.replaceChildren();
    for (const key of ['mail.send','codes.issue','gacha.enable','events.set','accounts.find','notice.publish','capacity.set']) {
      if (!operations[key])continue;
      const btn=node('button',displayName(key));btn.type='button';btn.dataset.route=key;
      btn.onclick=()=>select(key);quick.append(btn);
    }
    home(); overview.refresh();
  } catch(error) { if (epoch === session) {token = ''; message(error.message);} }
};
el('logout').onclick = () => {
  ++session; ++selectionVersion; editor=null; picker=mailForm=null;draftDirty=delivered=false; token = ''; operations = {}; selected = ''; busy = false;
  el('filter').value = ''; el('token').value = '';
  for (const id of ['route','result','fields','operations']) el(id).replaceChildren();
  overview.reset(); el('workspace').hidden = true; el('logout').hidden = true;
  el('connect').hidden = false; el('token').focus(); message('');
};
el('filter').oninput = navigation;
el('operation').onsubmit = async event => {
  event.preventDefault(); const name = selected, epoch = session, op = operations[name];
  if (delivered || busy || !op || op.availability === 'stopped') return;
  const version=selectionVersion;
  if(forms.has(name) || name==='gacha.enable') {
    const target=editor;
    if(!target?.dirty)return;
    try {
      const body=target.values();
      if (name==='rules.schedule' && Object.keys(body).length===0) {
        target.dirty=false;el('execute').disabled=true;el('editor-feedback').textContent='没有待保存的修改';return;
      }
      if(name==='gacha.enable' && !window.confirm('确认开启选中的卡池？'))return;
      el('editor-feedback').textContent='正在保存…';
      busy=true;el('execute').disabled=true;el('fields').inert=true;
      const route='/admin/v1/operations/'+name;
      await request(route,'POST',JSON.stringify(body), name==='gacha.enable'?{}:{tag:target.tag});
      target.dirty=false;
      if(version!==selectionVersion)return;
      if(name.startsWith('gacha.'))await openGacha(name,version);else await openEditor(name,version);
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
    if(name==='codes.issue' && !JSON.parse(body).rewards?.length)throw Error('请选择至少一种奖励。');
    let prompt='确认执行「'+displayName(name)+'」？';
    if(name==='mail.send') prompt='向'+(mailForm.scope.value==='account'?mailForm.user.value:mailForm.scope.selectedOptions[0].textContent)+'发送「'+mailForm.title.value+'」？';
    if(name==='codes.issue') prompt='创建兑换码 '+el('field-code').value+'，'+(Number(el('field-quota').value)?'限 '+el('field-quota').value+' 人':'不限名额')+'？';
    const details=picker?.summary();
    if (!op.readOnly && !window.confirm([prompt,details].filter(Boolean).join('\n'))) return;
    busy = true; el('execute').disabled = true; message('');
    const path = '/admin/v1/operations/' + name + (op.readOnly && query ? '?' + query : '');
    const result = await request(path, op.readOnly ? 'GET' : 'POST', op.readOnly ? undefined : body);
    let catalog=null;
    if(['events.get','blessings.get','plaza.get'].includes(name) || (name==='rules.get' && result.dungeon_star_reward_id)) catalog=await request('/admin/v1/operations/'+name.replace('.get','.catalog'));
    if (name==='capacity.set' && epoch===session)await overview.refresh();
    if (epoch === session && version===selectionVersion) {el('output').hidden = false; if(name==='accounts.find')accountResults(result);
      else if(name==='mail.send'||name==='codes.issue'){
        delivered=true;draftDirty=false;el('fields').inert=true;
        el('result').textContent=name==='codes.issue'?'已创建兑换码 '+result.code:'已登记批次 '+result.id+'，发送时范围内共有 '+result.recipients_at_issue+' 名玩家。';
        const fresh=node('button',name==='mail.send'?'另写一封':'再创建一个');fresh.type='button';fresh.className='quiet light';fresh.onclick=()=>select(name,true);
        el('result').append(document.createTextNode('\n'),fresh);
      }
      else if(name==='mail.list'||name==='codes.list') rewardHistory(name,result);
      else if(name==='accounts.reset'){select('accounts.find',true);message('密码已重置，玩家需使用新密码登录。');}
      else el('result').replaceChildren(resultsFor(name,result,catalog,(route,row,key)=>{
        select(route);
        if(selected===route)el('field-'+key)?.formChoose?.(row);
      }));
      const resultRows=({'mail.list':'batches','reports.list':'reports','photos.list':'photos','maintenance.list':'notices'})[name];
      function pageButton(title, field, value) {
        const button=node('button',title); button.type='button';button.className='quiet light';
        button.onclick=()=>{field.value=value;el('operation').requestSubmit();};
        el('result').append(button);
      }
      if(resultRows){
        const rows=result[resultRows], last=rows.at(-1), before=el('field-before');
        if(before?.value)pageButton('返回最新记录',before,'');
        const limit=Number(el('field-limit')?.value || (name==='mail.list'?50:30));
        if(before && rows.length===limit)pageButton('查看更早记录',before,String(last.code_text || last.id || last.code));
      }
      if(name==='rewards.catalog'){
        const offset=el('field-offset'), start=Number(offset.value), limit=result.page_size;
        if(start>0)pageButton('上一页',offset,Math.max(0,start-limit));
        if(start+limit<result.total)pageButton('下一页',offset,start+limit);
      }
    }
  } catch(error) { if (epoch === session && version===selectionVersion) message(error.message); }
  finally { if (epoch === session) {busy = false; el('execute').disabled = delivered || operations[selected]?.availability === 'stopped' || (forms.has(selected) && !editor?.dirty);} }
};
