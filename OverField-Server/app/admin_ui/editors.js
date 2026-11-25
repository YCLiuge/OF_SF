const create = (tag, text) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};
function labeled(parent, title, control) {
  const label = create('label',title); label.append(control); parent.append(label); return control;
}
function choice(items, value, change) {
  const select = create('select');
  for (const [key,label] of items) select.add(new Option(label,String(key)));
  select.value = String(value); select.onchange = () => change(select.value);
  return select;
}
function input(type, value, change) {
  const e = create('input'); e.type = type; e.value = value ?? '';
  e.oninput = () => change(e.value); return e;
}
function numeric(parent,title,value,min,max,change) {
  const e = input('number',value,v => change(v === '' ? null : Number(v)));
  e.min=min; e.max=max; e.step=1; e.required=true; return labeled(parent,title,e);
}
function localDate(parent,title,value,change,required=false) {
  const e = input('datetime-local',(value || '').replace(' ','T'),v => change(v ? v.replace('T',' ').padEnd(19,':00') : null));
  e.step=1; e.required=required; labeled(parent,title,e); return e;
}
const kinds = {daily:'日常',sign_in:'签到',activity_gift:'活动礼包',battle_pass:'通行证',newcomer:'新人',photo_show:'摄影',turntable:'转盘',sandbag:'沙袋',anniversary_wish:'许愿',recall:'回归',invite:'邀请',koi:'锦鲤',event_dialogue:'剧情'};
function bannerName(row) {
  return ({2000:'常驻招募',2002:'新手招募',4000:'时装招募'})[row.id] || ('UP 招募 · 角色 ' + row.featured);
}
const STATES = [['stock','按原排期'],['on','启用'],['off','停用']];
function collection(group, catalog, current, mark) {
  const pools = group==='gacha', tree = group==='blessings';
  const key = pools?'pools':tree?'trees':'events', all = pools?catalog.banners:catalog[key];
  const original = current[key], rows = new Map(original.map(r=>[r.id,structuredClone(r)]));
  const entries = new Map(all.map(r=>[r.id,r]));
  const host=create('div'), tools=create('div'), details=create('div'), status=create('p');
  const listing=create('select'); listing.size=7; listing.setAttribute('aria-label','选择目录项目'); listing.className='catalog-list';
  const search=input('search','',()=>list()); search.placeholder='名称、类型或编号'; search.setAttribute('aria-label','筛选目录');
  let id = original.find(r=>entries.has(r.id))?.id ?? all[0]?.id;
  let scope='all';
  const scopeControl=choice([['all','全部项目'],['override','已配置'],['on','已启用'],['off','已停用']],scope,v=>{scope=v;list();});
  scopeControl.setAttribute('aria-label','配置筛选');
  tools.className='editor-tools'; tools.append(search,scopeControl); status.className='hint'; details.className='editor-detail';
  host.append(tools,status,listing,details);
  const state = id => !rows.has(id)?'stock':rows.get(id).enabled?'on':'off';
  const stateName = id => !rows.has(id)?(pools?'未配置':'按原排期'):rows.get(id).enabled?'已启用':'已停用';
  function list() {
    const term=search.value.trim().toLowerCase();
    const shown=all.filter(r=> (scope==='all' || (scope==='override'?rows.has(r.id):state(r.id)===scope)) &&
      [r.id,r.name || bannerName(r),kinds[r.kind] || r.kind].join(' ').toLowerCase().includes(term));
    status.textContent=shown.length+' 项匹配，区服已配置 '+rows.size+' 项';
    listing.replaceChildren(...shown.map(r=>new Option((r.name || bannerName(r))+' · '+r.id+' · '+stateName(r.id),String(r.id))));
    if (!shown.some(r=>r.id===id)) id=shown[0]?.id;
    if (id !== undefined) listing.value=String(id);
    render();
  }
  function render() {
    details.replaceChildren();
    const spec=entries.get(id); if (!spec) {details.append(create('p','没有匹配项目'));return;}
    const row=rows.get(id);
    details.append(create('h2',spec.name || bannerName(spec)));
    const start=spec.opens_at ?? spec.opening, end=spec.closes_at ?? spec.closing;
    details.append(create('p',(spec.kind?(kinds[spec.kind] || spec.kind)+' · ':'')+(start || '不限开始时间')+' 至 '+(end || '不限结束时间')+'（北京时间）'));
    const modes=pools?[['stock','从区服配置移除'],['on','启用'],['off','停用']]:STATES;
    const mode=labeled(details,'区服设置',choice(modes,state(id),v=>{
      if (v==='stock') rows.delete(id);
      else {
        const base=pools? structuredClone(catalog.presets?.[String(id)] || {id,pity:70,weights:{}}) : {id,opens_at:null,closes_at:null};
        rows.set(id,{...(row || base),enabled:v==='on'});
      }
      mark(); list();
    }));
    mode.dataset.control='mode';
    if (!row) return;
    if (!pools) {
      const dates=create('div'); dates.className='editor-grid';
      localDate(dates,'自定义开始时间',row.opens_at,v=>{row.opens_at=v; mark();});
      localDate(dates,'自定义结束时间',row.closes_at,v=>{row.closes_at=v; mark();}); details.append(dates);
      if (!tree && spec.kind==='activity_gift') {
        labeled(details,'礼包发放',choice([['stock','按区服默认'],['true','分阶段发放'],['false','完整发放']],row.staged_gifts===undefined?'stock':String(row.staged_gifts),v=>{
          if(v==='stock') delete row.staged_gifts; else row.staged_gifts=v==='true'; mark();
        }));
      }
      return;
    }
    const rates=create('div'); rates.className='editor-grid';
    numeric(rates,'高稀有度保底抽数',row.pity,1,1000,v=>{row.pity=v;mark();});
    if (id===2000) {
      const e=numeric(rates,'SR 保底（可留空）',row.sr_pity,0,1000,v=>{if(v===null) delete row.sr_pity; else row.sr_pity=v; mark();});
      e.required=false;
    }
    const total=create('p');
    function showTotal() { total.textContent='奖励组权重合计 '+Object.values(row.weights).reduce((sum,w)=>sum+w,0)+' / 10000'; }
    for (const g of spec.groups) numeric(rates,'奖励组 '+g+'（万分比）',row.weights[g] || 0,0,10000,v=>{
      if (v===null) delete row.weights[g]; else row.weights[g]=v; showTotal(); mark();
    });
    showTotal(); details.append(rates,total);
  }
  listing.onchange=()=>{id=Number(listing.value); render();};list();
  return {host,values:()=>({[key]:[...rows.values()]})};
}
function plaza(catalog, current, mark) {
  const host=create('div'), body=create('div'), draft=structuredClone(current);
  let custom=Object.hasOwn(current,'theme');
  const option=(rows)=>rows.map(o=>[o.id,o.name+' · '+o.id]);
  labeled(host,'广场排期',choice([['stock','按原排期'],['custom','自定义海上舞台']],custom?'custom':'stock',v=>{custom=v==='custom';mark();render();}));
  host.append(body);
  function render() {
    body.replaceChildren(); if (!custom)return;
    draft.theme ??= catalog.themes[0].id; draft.arcade ??= 0; draft.activities ??= [];
    labeled(body,'舞台',choice(option(catalog.themes),draft.theme,v=>{draft.theme=Number(v);mark();}));
    labeled(body,'季节街机',choice([[0,'关闭街机'],...option(catalog.arcades)],draft.arcade,v=>{draft.arcade=Number(v);mark();}));
    const grid=create('div');grid.className='editor-grid';
    localDate(grid,'开始（北京时间）',draft.opens_at,v=>{draft.opens_at=v;mark();},true);
    localDate(grid,'结束（北京时间）',draft.closes_at,v=>{draft.closes_at=v;mark();},true);body.append(grid);
    const set=create('fieldset');set.className='entrances';set.append(create('legend','广场活动入口'));
    for(const row of catalog.activities) {
      const cb=create('input');cb.type='checkbox';cb.value=row.id;cb.checked=draft.activities.includes(row.id);
      cb.onchange=()=>{draft.activities=cb.checked?[...draft.activities,row.id]:draft.activities.filter(id=>id!==row.id);mark();};
      const label=create('label');label.append(cb,document.createTextNode(row.name+' · '+row.id));set.append(label);
    }
    body.append(set);
  }
  render();return {host, values:()=>custom?draft:{}};
}
const rules={
  public_furniture_limit:'每个官方频道的家具数量',public_furniture_scope:'官方频道家具额度',home_furniture_limit:'主人家园家具上限',
  visitor_furniture_limit:'访客家具上限',mp_team_members:'队伍人数',mp_team_name_length:'队名最大长度',mp_team_limit:'队伍数量上限',
  fishing_min_level:'钓鱼最低等级',starter_teachable_actions:'默认解锁可学习动作',monster_gift_percent:'怪物带礼物概率（%）',activity_fragment_percent:'户外碎片掉率（%）',
  activity_fragment_quantity:'每次碎片数量（0 沿用资源表）',ordinary_ground_drop_percent:'普通地面掉落概率（%）',dungeon_star_reward_id:'副本星级奖励模板',enable_collapse_rooms:'开放坍缩房间'
};
function ruleEditor(schema,current,mark) {
  const host=create('div'), changes={};host.className='editor-grid';
  function change(key,v){if(current[key]===v)delete changes[key];else changes[key]=v; mark();}
  for (const [key,spec] of Object.entries(schema.properties)) {
    if(spec.enum) labeled(host,rules[key]||key,choice(spec.enum.map(v=>[v,({channel:'每频道',realm:'全区服'})[v] || v]),current[key],v=>change(key,v)));
    else numeric(host,rules[key]||key,current[key],spec.minimum ?? 0,spec.maximum ?? 4294967294,v=>change(key,v));
  }
  return {host,values:()=>changes};
}
export function editorFor(group,schema,catalog,current,mark) {
  if(group==='rules')return ruleEditor(schema,current,mark);
  if(group==='plaza')return plaza(catalog,current,mark);
  return collection(group,catalog,current,mark);
}
