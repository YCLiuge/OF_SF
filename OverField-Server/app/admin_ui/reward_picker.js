/* The server publishes only templates claimable by this delivery channel. */
export function rewardPicker(channel, fetchPage) {
  function element(tag, text) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function button(text, action) {
    const el = element('button', text);
    el.type = 'button'; el.className = 'quiet light'; el.onclick = action;
    return el;
  }
  const host = element('fieldset'); host.className = 'reward-picker';
  host.append(element('legend',channel === 'mail' ? '物品附件' : '兑换奖励'));
  const search = element('input'); search.type = 'search';
  search.placeholder = '搜索物品名称或编号'; search.setAttribute('aria-label', search.placeholder);
  const group = element('select'); group.setAttribute('aria-label','奖励分类');
  group.add(new Option('全部分类', ''));
  const filters = element('div'); filters.className = 'reward-filters'; filters.append(search,group);
  const cards = element('div'); cards.className = 'reward-cards';
  const note = element('span'); note.setAttribute('role','status');
  let offset = 0, pageSize = 24, generation = 0, timer, rows = [];
  const prev = button('上一页', () => load(offset - pageSize));
  const next = button('下一页', () => load(offset + pageSize));
  const pager = element('div'); pager.className='reward-pages'; pager.append(note,prev,next);
  const basket = element('div'); basket.className = 'basket';
  const selected = new Map();
  const value = element('input'); value.type='hidden'; value.name='rewards'; value.dataset.type='array';
  host.append(filters, cards, pager, basket, value);

  function sync() {
    value.value = JSON.stringify([...selected].map(([item,row]) => ({item,quantity:row.quantity})));
    host.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function renderSelection() {
    basket.replaceChildren(element('h3','已选物品（'+selected.size+' / 16）'));
    for (const [id,row] of selected) {
      const line = element('div'); line.className='basket-row';
      const label = element('label', row.name+' · '+id);
      const count = element('input'); count.type='number'; count.min='1'; count.max=String(row.maximum);
      count.step='1'; count.required=true; count.value=String(row.quantity); count.dataset.count=String(id);
      count.setAttribute('aria-label',row.name+'数量'); count.title='可发放 1–'+row.maximum;
      count.oninput=()=>{row.quantity=count.valueAsNumber;sync();};
      const remove=button('移除',()=>{selected.delete(id); renderSelection(); draw(); sync();});
      remove.setAttribute('aria-label','移除 '+row.name); line.append(label,count,remove); basket.append(line);
    }
    if (!selected.size) basket.append(element('p',channel==='mail' ? '可以只发送正文。' : '请选择至少一种奖励。'));
  }
  function draw() {
    cards.replaceChildren();
    for (const row of rows) {
      const label=element('label'); label.className='reward-card';
      const check=element('input'); check.type='checkbox'; check.checked=selected.has(row.id);
      check.dataset.item=String(row.id);
      check.disabled = !check.checked && selected.size>=16;
      check.onchange=()=>{
        if(check.checked) selected.set(row.id,{...row,quantity:1});
        else selected.delete(row.id);
        renderSelection(); sync();
        for(const box of cards.querySelectorAll('input')) box.disabled = !box.checked && selected.size>=16;
      };
      const info=element('span'); info.append(element('strong',row.name),element('small',row.group+' · '+row.id));
      label.append(check,info); cards.append(label);
    }
    if(!rows.length) cards.append(element('p','没有匹配的物品，试试其他名称或分类。'));
  }
  function loading() {
    prev.disabled=next.disabled=true; cards.replaceChildren(); note.textContent='正在读取物品…';
  }
  async function load(start) {
    clearTimeout(timer);
    const serial=++generation; offset=Math.max(0,start); loading();
    const params=new URLSearchParams({channel,offset:String(offset)});
    if(group.value)params.set('group',group.value);
    if(search.value.trim())params.set('query',search.value.trim());
    try {
      const result=await fetchPage(params);
      if(serial!==generation)return;
      if(group.options.length===1)for(const title of result.groups)group.add(new Option(title,title));
      rows=result.items;pageSize=result.page_size;
      note.textContent=result.total ? (offset+1)+'–'+(offset+rows.length)+' / '+result.total : '共 0 项';
      prev.disabled=offset===0;next.disabled=offset+rows.length>=result.total;draw();
    } catch(error) {
      if(serial!==generation)return;
      rows=[];note.textContent='物品目录读取失败';cards.replaceChildren(element('p',error.message),button('重新读取',()=>load(offset)));
    }
  }
  search.oninput=event=>{event.stopPropagation();clearTimeout(timer);generation++;loading();timer=setTimeout(()=>load(0),180);};
  group.onchange=()=>load(0);
  value.value='[]';renderSelection();load(0);
  return {host, summary:()=>[...selected.values()].map(row=>row.name+' × '+row.quantity).join('、')};
}
