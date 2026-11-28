/* Pick actual realm records. Submit the server key through the normal schema form. */
function element(tag, text) {
  const e=document.createElement(tag);
  if(text!==undefined)e.textContent=text;
  return e;
}
function button(text, action) {
  const b=element('button',text);b.type='button';b.className='quiet light';b.onclick=action;return b;
}
function date(value) {
  if(!value)return '';
  if(typeof value==='number')return new Date(value<1e11?value*1000:value).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'});
  return String(value).split(' ')[0];
}
const sources={
  'notice.edit':{label:'选择公告',route:'maintenance.list',rows:'notices',key:'id',cursor:r=>r.id,title:r=>r.title,detail:r=>date(r.published)},
  'notice.withdraw':{label:'选择要撤回的公告',route:'maintenance.list',rows:'notices',key:'id',cursor:r=>r.id,title:r=>r.title,detail:r=>date(r.published)},
  'photos.withdraw':{label:'选择要撤回的照片',route:'photos.list',rows:'photos',key:'code',cursor:r=>r.code_text,title:r=>r.title||'未命名照片',detail:r=>[r.author,r.code_text?'分享码 '+r.code_text:'',date(r.posted_ms)].filter(Boolean).join(' · ')},
  'reports.resolve':{label:'选择待处理举报',route:'reports.list',rows:'reports',key:'id',cursor:r=>r.id,title:r=>r.reason||r.content||'玩家举报',detail:r=>[r.reporter_name?'举报人 '+r.reporter_name:'',r.target_name?'被举报人 '+r.target_name:'',r.content!==r.reason?r.content:'',date(r.created)].filter(Boolean).join(' · ')},
  'codes.withdraw':{label:'选择要停用的兑换码',route:'codes.list',rows:'codes',key:'code',title:r=>r.code,detail:r=>[r.kind==='inherit'?'继承奖励':'普通兑换',r.quota?'限 '+r.quota+' 人':'不限名额'].join(' · '),filter:r=>r.enabled}
};
function recordPicker(name,fields,request,spec) {
  const original=fields.querySelector('#field-'+spec.key);
  const wrap=original.parentElement,label=wrap.querySelector('label');
  label.textContent=spec.label;
  const search=element('input');search.type='search';search.placeholder='筛选已读取的记录';search.setAttribute('aria-label',search.placeholder);
  const pick=element('select');
  pick.id=original.id;pick.name=original.name;pick.required=true;pick.dataset.type=original.dataset.type;
  original.replaceWith(search,pick);
  const hint=element('p');hint.className='hint';hint.setAttribute('role','status');
  const more=button('读取更多记录',load);
  wrap.append(hint,more);
  const choices=new Map();
  const key=r=>String(spec.key==='code' && r.code_text?r.code_text:r[spec.key]);
  const format=r=>[spec.title(r),spec.detail(r)].filter(Boolean).join(' · ');
  let cursor='', busy=false, ended=false;
  function draw() {
    const retained=pick.value,term=search.value.trim().toLocaleLowerCase();
    const matches=[...choices.values()].filter(r=>key(r)===retained||format(r).toLocaleLowerCase().includes(term));
    pick.replaceChildren(new Option('请选择…',''),...matches.map(r=>new Option(format(r),key(r))));
    pick.value=retained;
    hint.textContent=matches.length?'可选 '+matches.length+' 条记录':ended?'没有符合条件的记录':'尚无匹配记录，可继续读取。';
    more.hidden=ended;
  }
  function chosen() {
    const r=choices.get(pick.value);
    if(name==='notice.edit'){
      fields.querySelector('#field-title').value=r?.title??'';
      fields.querySelector('#field-body').value=r?.content??'';
      const images=fields.querySelector('#field-images'),clear=fields.querySelector('#clear-images');
      if(images){images.value='';images.disabled=false;}
      if(clear)clear.checked=false;
    }
  }
  // List views can open an edit form with a record outside the first result page.
  pick.formChoose=r=>{
    if(spec.filter && !spec.filter(r))return;
    choices.set(key(r),r);search.value='';draw();pick.value=key(r);chosen();
  };
  async function load() {
    if(busy||ended)return;
    busy=true;more.disabled=true;hint.textContent='正在读取可选记录…';
    try{
      const query=new URLSearchParams();
      if(spec.cursor){query.set('limit','50');if(cursor)query.set('before',cursor);}
      const data=await request('/admin/v1/operations/'+spec.route+(query.size?'?'+query:''));
      if(!wrap.isConnected)return;
      const page=data[spec.rows];
      for(const r of page)if(!spec.filter||spec.filter(r))choices.set(key(r),r);
      ended=!spec.cursor || page.length<50;
      if(!ended)cursor=String(spec.cursor(page[page.length-1]));
      more.textContent='读取更多记录';draw();
    }catch(error){
      hint.textContent='记录读取失败：'+error.message;more.hidden=false;more.textContent='重新读取';
    }finally{busy=false;more.disabled=false;}
  }
  pick.add(new Option('请选择…',''));pick.onchange=chosen;search.oninput=draw;
  // A record opened from a result card is already available before the first paint.
  queueMicrotask(()=>{if(wrap.isConnected && !pick.value)load();});
}
function recipients(fields,request,mail) {
  if(!mail)return;
  const box=element('div'),toggle=button('通过昵称或 UID 查找玩家',()=>{panel.hidden=!panel.hidden;if(!panel.hidden)term.focus();});
  const panel=element('fieldset');panel.hidden=true;panel.append(element('legend','查找收件人'));
  const kind=element('select');kind.setAttribute('aria-label','匹配字段');
  for(const [v,n] of [['nickname','游戏昵称'],['uid','公开 UID'],['username','登录账号']])kind.add(new Option(n,v));
  const term=element('input');term.type='search';term.placeholder='输入完整名称或 UID';term.setAttribute('aria-label','收件人查询内容');
  const results=element('div');results.setAttribute('aria-live','polite');
  let epoch=0;
  async function find(){
    const value=term.value.trim();if(!value)return;
    const n=++epoch;
    results.textContent='正在查找…';
    try{
      const query=new URLSearchParams({field:kind.value,value});
      const data=await request('/admin/v1/operations/accounts.find?'+query);
      if(!box.isConnected||epoch!==n)return;
      results.replaceChildren(...data.accounts.map(a=>button([a.nickname||'未建角色',a.username,a.uid?'UID '+a.uid:''].filter(Boolean).join(' · '),()=>{
        mail.user.value=a.username;mail.user.dispatchEvent(new Event('input',{bubbles:true}));panel.hidden=true;
      })));
      if(!data.accounts.length)results.textContent='未找到匹配的玩家。';
    }catch(error){if(epoch===n)results.textContent=error.message;}
  }
  term.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();find();}};
  for(const control of [kind,term])control.oninput=()=>{epoch++;results.replaceChildren();};
  panel.append(kind,term,button('查找玩家',find),results);box.append(toggle,panel);
  mail.user.closest('label').after(box);
  const sync=()=>{box.hidden=mail.scope.value!=='account';if(box.hidden){panel.hidden=true;epoch++;results.replaceChildren();}};
  mail.scope.addEventListener('change',sync);sync();
}
function rewardGroups(fields,request){
  const group=fields.querySelector('#field-group'), channel=fields.querySelector('#field-channel');
  channel.value='mail';
  const options=element('select');options.id=group.id;options.name=group.name;options.dataset.type='string';group.replaceWith(options);
  let epoch=0;
  const retry=button('重新读取分类',load);retry.hidden=true;options.after(retry);
  async function load(){
    const n=++epoch;
    options.replaceChildren(new Option('全部分类',''));retry.hidden=true;
    if(!channel.value)return;
    try{
      const data=await request('/admin/v1/operations/rewards.catalog?'+new URLSearchParams({channel:channel.value}));
      if(!options.isConnected||epoch!==n)return;
      for(const name of data.groups)options.add(new Option(name,name));
    }catch(error){if(epoch===n){retry.hidden=false;retry.title=error.message;}}
  }
  channel.addEventListener('change',load);load();
}
export function enhanceFields(name,fields,request,mail){
  const before=fields.querySelector('#field-before');
  if(before){before.type='hidden';before.parentElement.hidden=true;}
  const offset=fields.querySelector('#field-offset');
  if(name==='rewards.catalog' && offset){offset.type='hidden';offset.parentElement.hidden=true;}
  if(sources[name])recordPicker(name,fields,request,sources[name]);
  if(name==='mail.send')recipients(fields,request,mail);
  if(name==='rewards.catalog')rewardGroups(fields,request);
  const limit=fields.querySelector('#field-limit');
  if(limit && name.endsWith('.list')){
    limit.type='number';limit.removeAttribute('pattern');limit.min='1';limit.max='100';limit.placeholder='30';
  }
  if(name==='release.publish'){
    const path=fields.querySelector('#field-file'),sha=fields.querySelector('#field-sha256');
    path.placeholder='已放在服务器上的发行包路径';sha.pattern='[a-fA-F0-9]{64}';sha.title='完整的 64 位 SHA-256 校验值';
  }
}
