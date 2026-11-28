import {ruleLabels,eventKinds} from './editors.js';

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
const number = value => Number(value).toLocaleString('zh-CN');
function time(value) {
  if (!value) return '未设置';
  if (typeof value === 'number') return new Date(value < 1e11 ? value * 1000 : value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
  return value;
}
const period = row => [row.opens_at || '不限开始时间', row.closes_at || '不限结束时间'].join(' 至 ');
const named = (rows,id) => rows?.find(row=>row.id===id)?.name || ('未收录项目（'+id+'）');
function facts(pairs) {
  const list = element('dl',undefined,'result-facts');
  for (const [label,value] of pairs) {
    if (value !== undefined && value !== null && value !== '') list.append(element('dt',label),element('dd',String(value)));
  }
  return list;
}
function card(title, subtitle) {
  const box=element('section',undefined,'record-card');
  box.append(element('h3',title));
  if (subtitle) box.append(element('small',subtitle));
  return box;
}
function filterList(records, render, title, emptyText) {
  const host=element('div'), tools=element('div',undefined,'list-tools'), input=element('input'), count=element('span');
  input.type='search'; input.placeholder='搜索'+title;input.setAttribute('aria-label',input.placeholder);
  const list=element('div',undefined,'record-grid'), more=element('button','显示更多','quiet light');
  more.type='button'; let limit=18;
  function draw(){
    const query=input.value.trim().toLowerCase(), rows=records.filter(r=>r.search.toLowerCase().includes(query));
    count.textContent=rows.length+' 项'; list.replaceChildren(...rows.slice(0,limit).map(r=>render(r.row)));
    more.hidden=limit>=rows.length;
    if (!rows.length) list.append(element('p', query?'没有匹配的记录。':emptyText || '暂无记录。','list-empty'));
  }
  input.oninput=()=>{limit=18;draw();};more.onclick=()=>{limit+=18;draw();};
  tools.append(input,count);if(records.length>6)host.append(tools);
  host.append(list,more);draw();return host;
}
const records = (rows, search, render, title, empty) => filterList(rows.map(row=>({row,search:search(row)})),render,title,empty);
function rewardsFor(row,names,index) {
  const items=row.items || row.a_activity_gift_reward_item || row.full_server_activity_reward_item || row.recall_activity_reward_item || [row];
  const title=row.day?'第 '+row.day+' 天':row.level?'第 '+row.level+' 级':row.tier?'第 '+row.tier+' 档':'第 '+(index+1)+' 项';
  const descriptions=items.filter(i=>i.item_i_d || i.ticket).map(i=>{
    const id=i.item_i_d || i.ticket;
    return (names[id] || '未命名物品（'+id+'）')+' × '+number(i.item_num ?? i.count ?? 1);
  });
  if(row.limit_num)descriptions.push('限领 '+number(row.limit_num)+' 次');
  if(row.required)descriptions.push('需要 '+number(row.required)+' 点');
  return element('li',title+' · '+descriptions.join('、'));
}
function events(name,data,catalog) {
  const isTrees=name.startsWith('blessings.'), key=isTrees?'trees':'events', stock=name.endsWith('.catalog');
  const rows=data[key], source=catalog || data, index=new Map(source[key].map(r=>[r.id,r]));
  const names=source.item_names || {};
  return records(rows,r=>[(index.get(r.id)||r).name,r.id].join(' '),row=>{
    const original=index.get(row.id) || row;
    const title=original.name || '未收录期次（'+row.id+'）';
    const box=card(title, isTrees?'祈愿树':eventKinds[original.kind] || '活动');
    const effective={opens_at:row.opens_at || original.opens_at,closes_at:row.closes_at || original.closes_at};
    box.append(facts([['北京时间',period(effective)],['区服安排',stock?null:row.enabled?'启用':'停用'],
      ['兑换节点',original.nodes],['货币',original.currency_ids?.map(id=>names[id]||'未命名货币（'+id+'）').join('、')],
      ['投稿截止',original.submit_until],['结算时间',original.settle_at],
      ['分阶段发放礼包',row.staged_gifts===undefined?null:row.staged_gifts?'是':'否']]));
    if(original.rewards?.length){
      const details=element('details'), list=element('ul');details.append(element('summary','查看奖励（'+original.rewards.length+' 项）'),list);
      details.ontoggle=()=>{if(details.open && !list.childElementCount)list.append(...original.rewards.map((r,i)=>rewardsFor(r,names,i)));};
      box.append(details);
    }
    return box;
  },isTrees?'祈愿树':'活动','沿用游戏原排期，当前没有单独设置的期次。');
}
function releaseInfo(r) {
  const box=card('登陆器 '+r.version,r.filename);
  box.append(facts([['文件大小',(Number(r.size)/1048576).toFixed(1)+' MiB'],['发布时间',time(r.published)],['更新说明',r.notes],['SHA-256',r.sha256]]));
  return box;
}
const states={running:'运行中',stopping:'正在停服',stopped:'已停止',busy:'维护中',uninitialized:'尚未初始化',stale:'上次退出异常'};
const extraRules={public_min:'公共频道起始号', public_max:'公共频道结束号', uid_start:'新角色起始 UID',world_min:'世界聊天起始频道',world_max:'世界聊天结束频道',game_connections:'同时连接上限'};
const reportCategories=['昵称违规','聊天违规','广告','诈骗','外挂','个性签名','其他','家具','分享照片'];
const chatChannels=['默认频道','聊天室','私聊','系统','公告','队伍','公会','喇叭'];
function recordAction(box,label,route,row,key,choose) {
  if(!choose)return;
  const button=element('button',label,'quiet light');
  button.type='button';button.onclick=()=>choose(route,row,key);box.append(button);
}
function player(name,uid) {
  return name ? name+'（UID '+uid+'）' : 'UID '+uid;
}
export function resultsFor(name,data,catalog,choose) {
  if(/^(events|blessings)\.(get|catalog)$/.test(name))return events(name,data,catalog);
  switch(name){
  case 'plaza.catalog':{
    const box=element('div');for(const [key,title] of [['themes','舞台主题'],['activities','活动入口'],['arcades','街机']]) {
      box.append(element('h3',title),records(data[key],r=>r.name,r=>card(r.name,r.opens_at?period(r):''),title));
    }return box;
  }
  case 'plaza.get':
    return !data.theme?element('p','当前使用游戏原排期。'):facts([['舞台主题',named(catalog.themes,data.theme)],['北京时间',period(data)],
      ['街机',data.arcade?named(catalog.arcades,data.arcade):'关闭'],['开放入口',data.activities.map(id=>named(catalog.activities,id)).join('、') || '无']]);
  case 'rules.get': {
    const names={...extraRules,...ruleLabels};
    const pairs=Object.entries(data).map(([key,v])=>{
      if(key==='public_furniture_scope')v=v==='channel'?'按频道':'全区服共用';
      if(['enable_collapse_rooms','starter_teachable_actions'].includes(key))v=v?'开启':'关闭';
      if(key==='dungeon_star_reward_id')v=v?(catalog?.templates?.find(r=>r.id===v)?.name || '奖励套餐 '+v):'使用游戏默认奖励';
      return [names[key] || key,v];
    });
    return facts(pairs);
  }
  case 'realm.status':return facts([['服务状态',states[data.lifecycle]],['存档',data.initialized?'已初始化':'尚未初始化'],['注册账号',data.accounts],['游戏角色',data.characters],['存档位置',data.state]]);
  case 'capacity.set':case 'capacity.get':return facts([['同时连接上限',data.limit],['当前连接',data.used ?? '暂无在线统计']]);
  case 'reports.list':return records(data.reports,r=>[r.reason,r.content,r.reporter_name,r.target_name,r.reporter,r.target,r.photo_text,reportCategories[r.category]].join(' '),r=>{
    const box=card(r.reason || '玩家举报',time(r.created));
    box.append(facts([['举报类别',reportCategories[r.category]],['举报人',player(r.reporter_name,r.reporter)],
      ['被举报玩家',player(r.target_name,r.target)],['举报内容',r.content],['聊天频道',r.category===1?chatChannels[r.channel]:null],
      ['照片分享码',r.photo_text],['处理状态',r.resolved?'已处理':'待处理']]));
    if(!r.resolved)recordAction(box,'处理举报','reports.resolve',r,'id',choose);
    return box;
  },'举报','当前条件下没有举报。');
  case 'photos.list':return records(data.photos,r=>[r.author,r.title,r.code_text || r.code].join(' '),r=>{
    const box=card(r.title || '未命名照片',r.author);
    box.append(facts([['发布时间',time(r.posted_ms)],['分享码',r.code_text || r.code]]));
    recordAction(box,'撤回照片','photos.withdraw',r,'code',choose);return box;
  },'照片','暂无已发布的照片。');
  case 'maintenance.list':{
    const box=element('div');box.append(element('h3','当前登陆器版本'),data.release?releaseInfo(data.release):element('p','尚未发布登陆器。'),element('h3','已发布公告'));
    box.append(records(data.notices,r=>r.title+' '+r.content,r=>{
      const n=card(r.title,time(r.published));n.append(element('p',r.content));
      if(r.images?.length)n.append(element('small','附图 '+r.images.length+' 张'));
      recordAction(n,'编辑公告','notice.edit',r,'id',choose);
      recordAction(n,'撤回公告','notice.withdraw',r,'id',choose);
      return n;
    },'公告','暂无已发布公告。'));return box;
  }
  case 'rewards.catalog': {
    const box=element('div');box.append(element('p','共找到 '+number(data.total)+' 种物品。'));
    box.append(records(data.items,r=>r.name,r=>card(r.name,r.group+' · 单次最多 '+number(r.maximum)), '物品','没有符合条件的物品。'));return box;
  }
  case 'release.publish':return releaseInfo(data);
  case 'reports.resolve':return element('p','已将这条举报标记为已处理。');
  case 'photos.withdraw':return element('p','照片已从公开照片墙撤回。');
  case 'codes.withdraw':return element('p','已停用兑换码 '+data.code+'。');
  case 'notice.publish':return element('p','公告已发布，可在「查看已发布内容」核对。');
  case 'notice.edit':return element('p','公告修改已保存。');
  case 'notice.withdraw':return element('p','公告已撤回。');
  case 'release.withdraw':return element('p','已撤回当前发布的登陆器版本。');
  case 'realm.stop':return element('p','停服请求已送达，区服正在保存。');
  default:return element('p','操作已完成。');
  }
}
