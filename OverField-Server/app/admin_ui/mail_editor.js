/* Native in-game mail, composed using the owner console form. */
export function composeMail(host) {
  host.replaceChildren();
  function add(tag, text) {
    const element=document.createElement(tag);
    if (text !== undefined) element.textContent=text;
    return element;
  }
  function label(title, input) {
    const row=add('label',title);
    row.append(input);
    host.append(row);
    return row;
  }
  function input(name,type='text',required=true) {
    const element=add('input');
    element.type=type; element.name=name; element.required=required;
    element.dataset.type=type==='number'?'integer':'string';
    return element;
  }
  const scope=add('select'); scope.name='scope'; scope.dataset.type='string';
  for(const [id,name] of [['account','指定账号'],['existing','发送时已有的玩家'],['ongoing','已有玩家和有效期内新建的玩家']])
    scope.add(new Option(name,id));
  label('收件范围',scope);
  const user=input('username'); const recipient=label('玩家登录账号',user);
  scope.onchange=()=>{const single=scope.value==='account'; recipient.hidden=!single;user.required=single;if(!single)user.value='';};
  const title=input('title');title.maxLength=100;label('邮件标题',title);
  const sender=input('sender');sender.maxLength=50;sender.value='区服管理';label('发件人',sender);
  const body=add('textarea');body.name='body';body.dataset.type='string';body.required=true;body.maxLength=2000;body.rows=4;label('正文',body);
  const days=input('days','number');days.value='30';days.min='1';days.max='365';label('有效期（从发送开始计算，天）',days);
  const attachments=add('fieldset'),rows=add('div');attachments.append(add('legend','物品附件'),rows);
  const button=add('button','添加附件');button.type='button';button.className='quiet light';attachments.append(button);host.append(attachments);
  const data=input('rewards','hidden',false);data.dataset.type='array';data.value='[]';host.append(data);
  const update=()=>{
    data.value=JSON.stringify(Array.from(rows.children,r=>({item:Number(r.querySelector('[data-item]').value),quantity:Number(r.querySelector('[data-count]').value)})));
    button.disabled=rows.children.length>=16;
  };
  button.onclick=()=>{
    if (rows.children.length>=16)return;
    const line=add('div');line.className='editor-grid';
    const item=add('input');item.type='number';item.min='1';item.max='4294967295';item.required=true;item.placeholder='物品模板编号';item.setAttribute('aria-label','物品模板编号');item.dataset.item='';
    const qty=add('input');qty.type='number';qty.min='1';qty.max='1000000';qty.required=true;qty.value='1';qty.setAttribute('aria-label','物品数量');qty.dataset.count='';
    const remove=add('button','移除附件');remove.className='quiet light';remove.type='button';
    remove.onclick=()=>{line.remove();update();};item.oninput=qty.oninput=update;
    line.append(item,qty,remove);rows.append(line);update();item.focus();
  };
  const key=input('request_id');key.readOnly=true;key.value=crypto.randomUUID();
  label('投递批次号（可用于核对发送记录）',key);
  return {key,scope,user,title};
}

