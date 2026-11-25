const el = id => document.getElementById(id);
const groups = {
  realm:'区服', maintenance:'运营概览', notice:'公告', release:'版本', events:'活动', plaza:'海上舞台',
  gacha:'招募', blessings:'祈愿树', rules:'玩法规则', codes:'兑换码', reports:'举报', photos:'照片墙', state:'备份'
};
const actions = {list:'列表', get:'查看配置', set:'修改配置', catalog:'内容目录', edit:'编辑', issue:'创建', publish:'发布',
  withdraw:'撤回', enable:'开启卡池', resolve:'标记已处理', backup:'备份存档', restore:'还原存档', status:'运行状态', stop:'正常停服'};
const labels = {id:'编号',file:'服务器上的文件路径',archive:'服务器上的备份路径',title:'标题',body:'正文',images:'服务器上的图片路径',
  version:'版本号',notes:'更新说明',sha256:'SHA-256',limit:'每页条数',before:'上一页最后一项编号',enabled:'启用',
  code:'兑换码或分享码',quota:'总名额',kind:'类型',rewards:'奖励',expires_at:'到期时间',trees:'祈愿树排期',pools:'卡池配置',
  resolved:'已处理',events:'活动配置',stages:'舞台配置'};
const lifecycle = {running:'运行中',stopping:'正在停服',stopped:'已停服',busy:'维护中',uninitialized:'尚未初始化',stale:'上次退出异常'};
let token = '', operations = {}, selected = '', session = 0, busy = false;
const message = value => { el('status').textContent = value; };
function node(tag, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}
function displayName(name) {
  const [group, action] = name.split('.');
  return (groups[group] || group) + ' · ' + (actions[action] || action);
}
async function request(path, method = 'GET', payload) {
  const headers = {Authorization: 'Bearer ' + token};
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, body: payload, credentials:'omit', cache:'no-store'});
  const body = await response.json();
  if (!response.ok || !body.ok) throw Error(body.error?.message || 'HTTP ' + response.status);
  return body.result;
}
class RealmOverview extends HTMLElement {
  connectedCallback() {
    const head = node('header'), refresh = node('button','刷新状态');
    refresh.className = 'quiet light'; refresh.type = 'button'; refresh.onclick = () => this.refresh();
    head.append(node('h2','区服概况'),refresh);
    this.values = []; const row = node('div'); row.className = 'metrics';
    for (const title of ['服务状态','注册账号','游戏角色']) {
      const cell = node('div'), value = node('b','—');
      cell.append(node('span', title), value); row.append(cell); this.values.push(value);
    }
    this.button = refresh; this.replaceChildren(head, row);
  }
  reset() { this.values.forEach(field => field.textContent = '—'); this.button.disabled = false; }
  async refresh() {
    const epoch = session; this.button.disabled = true;
    try {
      const data = await request('/admin/v1/operations/realm.status');
      if (epoch !== session) return;
      [lifecycle[data.lifecycle] || data.lifecycle, data.accounts, data.characters].forEach((value,index) => {
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
    for (const value of spec.enum || [true,false]) input.add(new Option(value === true ? '是' : value === false ? '否' : String(value),String(value)));
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
function select(name) {
  selected = name; const op = operations[name], schema = op.inputSchema;
  el('title').textContent = displayName(name); el('route').textContent = name;
  el('description').textContent = op.availability === 'stopped' ? '请在正常停服后通过服主 CLI 执行此操作。' : (/[\u3400-\u9fff]/.test(op.description) ? op.description : '');
  el('mode').textContent = op.readOnly ? '只读' : op.availability === 'stopped' ? '停服操作' : '更改区服';
  el('fields').replaceChildren(...Object.entries(schema.properties || {}).map(([key,spec]) => field(key,spec,schema.required?.includes(key) || false)));
  el('schema').textContent = (op.readOnly ? 'GET ' : 'POST ') + '/admin/v1/operations/' + name + '\n\n' + JSON.stringify(schema,null,2);
  el('output').hidden = true; el('result').textContent = '';
  el('execute').textContent = op.readOnly ? '查询' : op.availability === 'stopped' ? '请通过 CLI 执行' : displayName(name);
  el('execute').disabled = busy || op.availability === 'stopped'; message(''); navigation();
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
    select(operations['realm.status'] ? 'realm.status' : Object.keys(operations)[0]); overview.refresh();
  } catch(error) { if (epoch === session) {token = ''; message(error.message);} }
};
el('logout').onclick = () => {
  ++session; token = ''; operations = {}; selected = ''; busy = false;
  el('filter').value = ''; el('token').value = '';
  for (const id of ['schema','result','fields','operations']) el(id).replaceChildren();
  overview.reset(); el('workspace').hidden = true; el('logout').hidden = true;
  el('connect').hidden = false; el('token').focus(); message('');
};
el('filter').oninput = navigation;
el('operation').onsubmit = async event => {
  event.preventDefault(); const name = selected, epoch = session, op = operations[name];
  if (busy || !op || op.availability === 'stopped') return;
  try {
    const {body,query} = collect();
    if (!op.readOnly && !window.confirm('确认执行「' + displayName(name) + '」？')) return;
    busy = true; el('execute').disabled = true; message('');
    const path = '/admin/v1/operations/' + name + (op.readOnly && query ? '?' + query : '');
    const result = await request(path, op.readOnly ? 'GET' : 'POST', op.readOnly ? undefined : body);
    if (epoch === session && selected === name) {el('output').hidden = false; el('result').textContent = JSON.stringify(result,null,2);}
  } catch(error) { if (epoch === session) message(error.message); }
  finally { if (epoch === session) {busy = false; el('execute').disabled = operations[selected].availability === 'stopped';} }
};
