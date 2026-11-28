const create=(tag,text)=>{
  const node=document.createElement(tag);
  if(text!==undefined)node.textContent=text;
  return node;
};
function labeled(parent,title,control){
  const label=create('label',title);label.append(control);parent.append(label);return control;
}
function choice(items,value,change){
  const select=create('select');
  for(const [key,label] of items) select.add(new Option(label,String(key)));
  select.value=String(value);select.onchange=()=>change(select.value);
  return select;
}
function input(type,value,change){
  const element=create('input');element.type=type;element.value=value??'';
  element.oninput=()=>change(element.value);return element;
}
function localDate(parent,title,value,change,required=false){
  const control=input('datetime-local',(value||'').replace(' ','T'),v=>change(v ? v.replace('T',' ')+(v.length===16?':00':'') : null));
  control.step=1;control.required=required;return labeled(parent,title,control);
}
export const eventKinds={daily:'日常',sign_in:'签到',activity_gift:'活动礼包',battle_pass:'通行证',newcomer:'新人',
  photo_show:'摄影',turntable:'扭蛋机',sandbag:'沙袋',anniversary_wish:'许愿',recall:'回归',invite:'邀请',
  koi:'锦鲤',event_dialogue:'活动剧情',sakura_fireworks:'樱花烟花'};
const modes=[['stock','沿用游戏排期'],['on','启用'],['off','停用']];
const dateRange=row=>[row.opens_at?.slice(0,10)||'不限开始',row.closes_at?.slice(0,10)||'不限结束'].join(' 至 ');
function calendar(group,catalog,current,mark){
  const tree=group==='blessings',key=tree?'trees':'events',all=catalog[key];
  const rows=new Map(current[key].map(row=>[row.id,structuredClone(row)]));
  const entries=new Map(all.map(row=>[row.id,row]));
  const host=create('div'),tools=create('div'),details=create('div'),status=create('p');
  tools.className='editor-tools';status.className='hint';details.className='editor-detail';
  const listing=create('select');listing.size=7;listing.setAttribute('aria-label',tree?'祈愿树期数':'活动期数');listing.className='catalog-list';
  const search=input('search','',list);search.placeholder=tree?'搜索树名或开放日期':'搜索活动名、类型或开放日期';search.setAttribute('aria-label',search.placeholder);
  let id=current[key].find(row=>entries.has(row.id))?.id??all[0]?.id;
  const state=id=>!rows.has(id)?'stock':rows.get(id).enabled?'on':'off';
  const filters=choice([['all','全部期数'],['override','已自定义'],['on','已启用'],['off','已停用']],'all',list);
  filters.setAttribute('aria-label','配置筛选');
  const stateName=id=>!rows.has(id)?'原排期':rows.get(id).enabled?'已启用':'已停用';
  tools.append(search,filters);host.append(tools,status,listing,details);
  function list(){
    const term=search.value.trim().toLocaleLowerCase(),scope=filters.value;
    const shown=all.filter(row=>(scope==='all'||(scope==='override'?rows.has(row.id):state(row.id)===scope))&&
      [row.name,eventKinds[row.kind],dateRange(row),row.id].join(' ').toLocaleLowerCase().includes(term));
    status.textContent='找到 '+shown.length+' 期 · 已自定义 '+rows.size+' 期';
    listing.replaceChildren(...shown.map(row=>new Option([row.name,dateRange(row),stateName(row.id)].join(' · '),String(row.id))));
    if(!shown.some(row=>row.id===id))id=shown[0]?.id;
    if(id!==undefined)listing.value=String(id);
    render();
  }
  function render(){
    details.replaceChildren();
    const spec=entries.get(id);if(!spec){details.append(create('p','没有匹配项目'));return;}
    const row=rows.get(id),kind=tree?'祈愿树':eventKinds[spec.kind]||'活动';
    details.append(create('h2',spec.name),create('p',kind+' · 原开放时间 '+dateRange(spec)+'（北京时间）'));
    const mode=labeled(details,'开放方式',choice(modes,state(id),v=>{
      if(v==='stock')rows.delete(id);
      else rows.set(id,{...(row||{id,opens_at:null,closes_at:null}),enabled:v==='on'});
      mark();list();
    }));
    mode.dataset.control='mode';
    if(!row||!row.enabled)return;
    const dates=create('div');dates.className='editor-grid';
    localDate(dates,'自定义开始（留空沿用原排期）',row.opens_at,v=>{row.opens_at=v;mark();});
    localDate(dates,'自定义结束（留空沿用原排期）',row.closes_at,v=>{row.closes_at=v;mark();});
    details.append(dates);
    if(!tree&&spec.kind==='activity_gift'){
      labeled(details,'礼包发放方式',choice([['stock','沿用默认'],['true','分阶段发放'],['false','完整发放']],row.staged_gifts===undefined?'stock':String(row.staged_gifts),v=>{
        if(v==='stock')delete row.staged_gifts;else row.staged_gifts=v==='true';mark();
      }));
    }
  }
  listing.onchange=()=>{id=Number(listing.value);render();};
  list();return {host,values:()=>({[key]:[...rows.values()]})};
}
function plaza(catalog,current,mark){
  const host=create('div'),body=create('div'),draft=structuredClone(current);
  let custom=Object.hasOwn(current,'theme');
  const option=rows=>rows.map(row=>[row.id,row.name]);
  labeled(host,'广场排期',choice([['stock','沿用游戏排期'],['custom','自定义海上舞台']],custom?'custom':'stock',v=>{custom=v==='custom';mark();render();}));
  host.append(body);
  function render(){
    body.replaceChildren();if(!custom)return;
    draft.theme??=catalog.themes[0].id;draft.arcade??=0;draft.activities??=[];
    labeled(body,'舞台外观',choice(option(catalog.themes),draft.theme,v=>{draft.theme=Number(v);mark();}));
    labeled(body,'季节街机',choice([[0,'关闭街机'],...option(catalog.arcades)],draft.arcade,v=>{draft.arcade=Number(v);mark();}));
    const grid=create('div');grid.className='editor-grid';
    localDate(grid,'开始（北京时间）',draft.opens_at,v=>{draft.opens_at=v;mark();},true);
    localDate(grid,'结束（北京时间）',draft.closes_at,v=>{draft.closes_at=v;mark();},true);body.append(grid);
    const set=create('fieldset');set.className='entrances';set.append(create('legend','广场活动入口'));
    const list=create('div');
    const search=input('search','',()=>showList());search.placeholder='搜索活动入口';search.setAttribute('aria-label',search.placeholder);
    set.append(search,list);body.append(set);
    function showList(){
      list.replaceChildren();
      const term=search.value.trim().toLocaleLowerCase();
      for(const row of catalog.activities.filter(r=>(r.name+' '+(r.opens_at||'')).toLocaleLowerCase().includes(term))){
        const box=create('input');box.type='checkbox';box.value=row.id;box.checked=draft.activities.includes(row.id);
        box.onchange=()=>{draft.activities=box.checked?[...draft.activities,row.id]:draft.activities.filter(id=>id!==row.id);mark();};
        const label=create('label');label.append(box,document.createTextNode([row.name,row.opens_at?.slice(0,10)].filter(Boolean).join(' · ')));list.append(label);
      }
      if(!list.childElementCount)list.append(create('p','没有找到活动入口'));
    }
    showList();
  }
  render();return {host,values:()=>custom?draft:{}};
}
export const ruleLabels={
  public_min:'公共频道起点',public_max:'公共频道终点',world_min:'世界频道起点',world_max:'世界频道终点',
  uid_start:'角色 UID 起点',game_connections:'同时连接上限',
  public_furniture_limit:'公共频道家具上限',public_furniture_scope:'公共家具额度统计范围',home_furniture_limit:'主人家园家具上限',
  visitor_furniture_limit:'访客家具上限',mp_team_members:'每队人数',mp_team_name_length:'队名最长字数',mp_team_limit:'队伍数量上限',
  fishing_min_level:'钓鱼最低生活等级',starter_teachable_actions:'新角色可学习动作',monster_gift_percent:'怪物携带礼物的概率（%）',
  activity_fragment_percent:'活动碎片掉落概率（%）',activity_fragment_quantity:'每次掉落碎片数量（0 沿用资源表）',
  ordinary_ground_drop_percent:'普通地面掉落概率（%）',dungeon_star_reward_id:'副本星级备用奖励',enable_collapse_rooms:'坍缩房间'
};
const limits={fishing_min_level:[1,10],mp_team_members:[2,16],mp_team_name_length:[1,20],
  monster_gift_percent:[0,100],activity_fragment_percent:[0,100],ordinary_ground_drop_percent:[0,100]};
const ruleSections={
  '家园与公共频道':['home_furniture_limit','visitor_furniture_limit','public_furniture_scope','public_furniture_limit'],
  '队伍与探索':['mp_team_members','mp_team_limit','mp_team_name_length','fishing_min_level','starter_teachable_actions','enable_collapse_rooms'],
  '掉落与奖励':['monster_gift_percent','activity_fragment_percent','activity_fragment_quantity','ordinary_ground_drop_percent','dungeon_star_reward_id']
};
function templatePicker(parent,templates,value,change){
  const defaults={id:0,name:'沿用副本原始奖励'};
  const byId=new Map(templates.map(r=>[r.id,r]));
  if(value&&!byId.has(value))byId.set(value,{id:value,name:'当前配置（未收录的模板）'});
  let selected=value;
  let page=0;
  const query=input('search','',()=>{page=0;render();});query.placeholder='按奖励物品名筛选';query.setAttribute('aria-label',query.placeholder);
  const list=create('select');list.setAttribute('aria-label','选择副本备用奖励组合');list.size=5;
  const note=create('small'),previous=create('button','上一页'),next=create('button','下一页');
  for(const control of [previous,next]){control.type='button';control.className='quiet light';}
  previous.onclick=()=>{page--;render();};next.onclick=()=>{page++;render();};
  function render(){
    const needle=query.value.trim().toLocaleLowerCase();
    const matches=templates.filter(row=>(row.name+' '+row.id).toLocaleLowerCase().includes(needle));
    const visible=new Map([[0,defaults]]);
    if(selected)visible.set(selected,byId.get(selected));
    for(const row of matches.slice(page*50,(page+1)*50))visible.set(row.id,row);
    list.replaceChildren(...[...visible.values()].map(row=>new Option(row.name+(row.id?' · 编号 '+row.id:''),String(row.id))));
    list.value=String(selected);
    previous.disabled=page===0;next.disabled=(page+1)*50>=matches.length;
    note.textContent=matches.length?'第 '+(page+1)+' / '+Math.ceil(matches.length/50)+' 页 · '+matches.length+' 种匹配组合':'没有匹配的组合';
  }
  list.onchange=()=>{selected=Number(list.value);change(selected);};
  const box=create('fieldset');box.append(create('legend',ruleLabels.dungeon_star_reward_id),query,list,previous,note,next);parent.append(box);render();
}
function ruleEditor(schema,catalog,current,mark){
  const host=create('div'),changes={};
  function change(key,value){if(current[key]===value)delete changes[key];else changes[key]=value;mark();}
  for(const [heading,keys] of Object.entries(ruleSections)){
    const section=create('section');section.append(create('h3',heading));const grid=create('div');grid.className='editor-grid';section.append(grid);host.append(section);
    for(const key of keys){
      const spec=schema.properties[key];if(!spec)continue;
      const title=ruleLabels[key],onChange=v=>change(key,v);
      if(key==='dungeon_star_reward_id'){templatePicker(section,catalog.templates,current[key],onChange);continue;}
      if(key==='starter_teachable_actions'||key==='enable_collapse_rooms'){
        const opts=key==='starter_teachable_actions'?[[0,'在游戏内学习'],[1,'默认解锁']]:[[0,'关闭'],[1,'开放']];
        labeled(grid,title,choice(opts,current[key],v=>onChange(Number(v))));
      }else if(spec.enum){
        labeled(grid,title,choice(spec.enum.map(v=>[v,({channel:'每个频道单独计算',realm:'全区服合计'})[v]||v]),current[key],onChange));
      }else{
        const control=input('number',current[key],v=>onChange(v===''?null:Number(v)));
        [control.min,control.max]=limits[key]||[spec.minimum??0,spec.maximum??4294967294];
        control.step=1;control.required=true;labeled(grid,title,control);
      }
    }
  }
  return {host,values:()=>changes};
}
export function editorFor(group,schema,catalog,current,mark){
  if(group==='rules')return ruleEditor(schema,catalog,current,mark);
  if(group==='plaza')return plaza(catalog,current,mark);
  return calendar(group,catalog,current,mark);
}
