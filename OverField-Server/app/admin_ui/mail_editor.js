/* Native in-game mail, composed using the owner console form. */
export function composeMail(host, picker) {
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
  host.append(picker.host);
  const key=input('request_id','hidden',false);key.value=crypto.randomUUID();host.append(key);
  return {key,scope,user,title};
}

