const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
function button(text, click) {
  const control = node('button', text);
  control.type = 'button';
  control.onclick = click;
  return control;
}
function select(label, options) {
  const field = node('select');
  field.setAttribute('aria-label',label);
  for (const [value,title] of options) field.add(new Option(title,value));
  return field;
}
function labeled(label, field) {
  const wrapper = node('label',label);
  wrapper.append(field);
  return wrapper;
}
function metric(label, value) {
  const cell = node('div');
  cell.append(node('dt',label),node('dd',value));
  return cell;
}
const perPage = 12;
const statusLabel = {on:'已开启',off:'已停用',unset:'未配置'};
const title = row => row.name || '未载入名称的卡池';
const caption = row => [row.actor, row.edition, row.kind].filter(Boolean).join(' · ');
const percent = points => Number((points/100).toFixed(2));

export function gachaPage(mode, catalog, current, mark = () => {}) {
  const enable = mode === 'enable', editable = mode === 'set';
  const draft = new Map(current.pools.map(row=>[row.id,structuredClone(row)]));
  const banners = catalog.banners;
  const byId = new Map(banners.map(b=>[b.id,b]));
  const host = node('div',null,'gacha-page');
  const tools = node('div',null,'gacha-tools');
  const search = node('input');
  search.type='search';search.placeholder='搜索卡池、探索员或映像名称';search.setAttribute('aria-label',search.placeholder);
  const types = select('招募类型',[['','全部类型'],...[...new Set(banners.map(b=>b.kind).filter(Boolean))].map(k=>[k,k])]);
  const filter = select('卡池状态',[['','全部状态'],...Object.entries(statusLabel)]);
  tools.append(search,types,filter);
  const count = node('p',null,'gacha-count'); count.setAttribute('aria-live','polite');
  const gallery = node('div',null,'gacha-gallery');
  const pages = node('div',null,'gacha-pager');
  const detail = node('section',null,'gacha-detail'); detail.setAttribute('aria-label','所选卡池');
  const grid = node('div',null,'gacha-layout');
  const list = node('div');list.append(count,gallery,pages);grid.append(list,detail);host.append(tools,grid);
  const target = node('input'); target.type='hidden';
  if (enable) {target.name='id';target.dataset.type='integer';host.append(target);}
  let selected, page=0;
  const state = id => !draft.has(id)?'unset':draft.get(id).enabled?'on':'off';

  function showDetail() {
    detail.replaceChildren();
    const row=byId.get(selected);
    if (!row) {
      detail.append(node('p',enable?'选择要开启的卡池':'请选择一个卡池查看详情','gacha-empty'));return;
    }
    const cfg=draft.get(selected);
    detail.append(node('h3',title(row)),node('p',caption(row),'gacha-subtitle'));
    const facts = node('dl',null,'gacha-facts');
    facts.append(metric('区服状态', statusLabel[state(selected)]),metric('招募道具',row.ticket_name || '招募券'));
    if (row.opening || row.closing) facts.append(metric('原版排期（北京时间）',(row.opening || '不限')+' 至 '+(row.closing || '不限')));
    detail.append(facts);
    if (enable) {
      detail.append(node('p',cfg?.enabled?'这个卡池已经开启。':'确认后，这一期卡池将在区服中开启。'));
      return;
    }
    if (editable) {
      const control=select('区服设置',[['on','开启'],['off','停用'],['unset','从区服配置移除']]);
      control.value=state(selected);control.dataset.control='mode';
      control.onchange=()=>{
        if (control.value==='unset') draft.delete(selected);
        else draft.set(selected,{...structuredClone(cfg || catalog.presets[selected]),enabled:control.value==='on'});
        mark();render(false);
      };
      detail.append(labeled('区服设置',control));
    }
    if (!cfg) {detail.append(node('p','此期尚未加入区服配置。'));return;}
    const settings = node('div',null,'editor-grid');
    function numeric(text,value,min,max,step,change,required=true) {
      const input=node('input');input.type='number';input.min=min;input.max=max;input.step=step;
      input.value=value ?? '';input.required=required;
      input.oninput=()=>{change(input.value===''?null:input.valueAsNumber);mark();};
      settings.append(labeled(text,input)); return input;
    }
    if (editable) {
      numeric('最高稀有度保底抽数',cfg.pity,1,1000,1,v=>{cfg.pity=v;}).readOnly=selected===4000;
      if (selected===2000) numeric('SR 保底抽数（可留空）',cfg.sr_pity,0,1000,1,v=>{
        if(v===null) delete cfg.sr_pity; else cfg.sr_pity=v;
      },false);
      detail.append(settings);
    } else {
      facts.append(metric('最高稀有度保底',cfg.pity+' 抽'));
      if (cfg.sr_pity!=null) facts.append(metric('SR 保底',cfg.sr_pity+' 抽'));
    }
    detail.append(node('h4','奖励概率'));
    const rewards = node('div',null,'gacha-rates'), total=node('p',null,'gacha-total');
    const sum=()=>Object.values(cfg.weights).reduce((n,v)=>n+v,0);
    function updateTotal(){
      const points=sum();
      total.textContent=Number.isFinite(points) ? '合计 '+percent(points)+'%'+(points===10000?'':' · 调整至 100% 后可保存') : '每项概率限 0 至 100%，最多两位小数';
      total.classList.toggle('invalid',sum()!==10000);
    }
    for(const key of row.groups) {
      const caption=row.group_names?.[key] || '其他奖励';
      if (!editable) {
        const line=node('div',null,'gacha-rate');line.append(node('span',caption),node('b',percent(cfg.weights[key] || 0)+'%'));rewards.append(line);continue;
      }
      const field=node('input');field.type='number';field.min=0;field.max=100;field.step='0.01';field.required=true;
      field.value=percent(cfg.weights[key] || 0);
      field.oninput=()=>{
        cfg.weights[key]=field.validity.valid ? Math.round(field.valueAsNumber*100) : NaN;
        updateTotal();mark();
      };
      rewards.append(labeled(caption+'（%）',field));
    }
    updateTotal();detail.append(rewards,total);
  }
  function render(resetSelection) {
    const term=search.value.trim().toLowerCase();
    const visible=banners.filter(b=>(!types.value || types.value===b.kind) && (!filter.value || filter.value===state(b.id)) &&
      [b.name,b.actor,b.alias,b.edition,b.kind,b.id].filter(v=>v!=null).join(' ').toLowerCase().includes(term));
    const maxPage=Math.max(1,Math.ceil(visible.length/perPage));
    page=Math.min(page,maxPage-1);
    const shown=visible.slice(page*perPage,(page+1)*perPage);
    if (resetSelection || !shown.some(b=>b.id===selected)) selected=enable?undefined:shown[0]?.id;
    target.value=selected ?? '';
    if (enable) mark(selected!==undefined && state(selected)!=='on');
    count.textContent='共 '+visible.length+' 个卡池';
    gallery.replaceChildren();pages.replaceChildren();
    if (!visible.length) gallery.append(node('p','没有符合条件的卡池。','gacha-empty'));
    for(const banner of shown) {
      const card=button(null,()=>{
        selected=banner.id;target.value=selected;
        for (const other of gallery.querySelectorAll('button')) other.setAttribute('aria-pressed',String(other===card));
        if (enable) mark(state(selected)!=='on');
        showDetail();
        if (window.matchMedia('(max-width:1000px)').matches) detail.scrollIntoView({block:'nearest'});
      });
      card.className='gacha-card';card.dataset.pool=banner.id;card.setAttribute('aria-pressed',String(banner.id===selected));
      if (banner.image) {
        const img=node('img');img.src=banner.image;img.alt='';img.loading='lazy';img.decoding='async';card.append(img);
      }
      const body=node('span',null,'gacha-card-body');
      body.append(node('small',banner.edition || banner.kind || '招募'),node('strong',title(banner)));
      if(banner.actor)body.append(node('span',banner.actor,'gacha-actor'));
      if(!banner.image) {
        body.append(node('span',banner.ticket_name || '招募券','gacha-actor'));
        const config=draft.get(banner.id);
        if(config)body.append(node('small',config.pity+' 抽保底'));
      }
      body.append(node('small',statusLabel[state(banner.id)],'gacha-state '+state(banner.id)));
      card.append(body); gallery.append(card);
    }
    if(maxPage>1){
      const prev=button('上一页',()=>{page--;render(true);}),next=button('下一页',()=>{page++;render(true);});
      prev.disabled=page===0;next.disabled=page===maxPage-1;pages.append(prev,node('span',(page+1)+' / '+maxPage),next);
    }
    showDetail();
  }
  function refilter(){page=0;render(true);}
  search.oninput=refilter;types.onchange=refilter;filter.onchange=refilter;
  render(true);
  return {host,values(){
    if(enable) {
      if(!target.value || state(selected)==='on')throw Error('请选择尚未开启的卡池。');
      return {id:selected};
    }
    const invalid=[...draft.values()].find(cfg=>{
      const weights=Object.values(cfg.weights);
      return !Number.isInteger(cfg.pity) || cfg.pity<1 || cfg.pity>1000 ||
        (cfg.sr_pity!=null && (!Number.isInteger(cfg.sr_pity)||cfg.sr_pity<0||cfg.sr_pity>1000)) ||
        weights.some(w=>!Number.isInteger(w)||w<0||w>10000) || weights.reduce((n,w)=>n+w,0)!==10000;
    });
    if (invalid) throw Error('请检查「'+title(byId.get(invalid.id))+'」的保底抽数和奖励概率，概率合计须为 100%。');
    return {pools:[...draft.values()]};
  }};
}
