"""Static, self-hosted console using the same JSON operations as the CLI and OpenAPI clients."""
HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OF_SF 区服管理</title><link rel="stylesheet" href="/admin/console.css">
<header><strong>OF_SF</strong><span>区服管理</span></header>
<main><form id="login"><h1>连接管理端口</h1><label for="token">服主令牌</label>
<input id="token" type="password" autocomplete="off" required><button>连接</button></form>
<section id="workspace" hidden><aside><h2>管理操作</h2><label for="filter">搜索操作</label>
<input id="filter" type="search" placeholder="如 plaza、events、公告"><nav id="operations"></nav>
<button id="logout" class="minor">断开连接</button></aside>
<article><h1 id="title">区服管理</h1><p id="description"></p><form id="operation">
<div id="fields"></div><button id="execute"></button></form>
<details><summary>JSON 输入格式</summary><pre id="schema"></pre></details>
<h2>执行结果</h2><pre id="result"></pre></article></section>
<p id="status" role="status" aria-live="polite"></p></main><script src="/admin/console.js" defer></script></html>"""

CSS = """*{box-sizing:border-box}body{margin:0;background:#f5f6f3;color:#20342f;font:16px/1.55 'Segoe UI',system-ui,sans-serif}
header{background:#133b35;color:white;padding:15px clamp(20px,5%,80px);display:flex;align-items:center;gap:20px}header strong{font:700 22px monospace;letter-spacing:1px}
main{max-width:1180px;margin:auto;padding:32px 22px}h1,h2{font-weight:600;line-height:1.25}h1{font-size:28px}h2{font-size:18px}
#login,article,aside{padding:24px;background:white;border:1px solid #d7dfda;border-radius:8px}
#login{max-width:460px;margin:8vh auto}#workspace{display:grid;grid-template-columns:minmax(210px,28%) 1fr;gap:18px;align-items:start}
[hidden]{display:none!important}nav{margin:14px 0;max-height:59vh;overflow:auto}button,input,select,textarea{font:inherit}
label{display:block;margin:12px 0 4px}input,textarea,select{width:100%;padding:8px;border:1px solid #91a69c;border-radius:4px;background:#fff}
textarea,pre{font-family:Consolas,monospace}button{border:0;padding:8px 16px;background:#17614f;color:white;border-radius:4px;cursor:pointer}
form>button{margin-top:16px}button:disabled{opacity:.55;cursor:default}button.minor,nav button{color:#245143;background:#edf2ef}
nav button{display:block;text-align:left;width:100%;margin:3px 0;padding:7px 10px}nav button[aria-current=true]{background:#d0e8df;color:#0a4636}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f2;padding:14px;border-radius:4px;max-height:45vh;overflow:auto}
details{margin:18px 0}#status{color:#694023}#status:not(:empty){padding:12px;background:#fff4d8}article{min-width:0}small{color:#52665b}
@media(max-width:760px){#workspace{grid-template-columns:1fr}nav{max-height:24vh}main{padding:16px}}"""

JS = r"""'use strict';
const el=id=>document.getElementById(id);
const labels={reports:'举报',codes:'兑换码',photos:'照片墙',plaza:'广场舞台',blessings:'祈愿树',events:'活动',gacha:'卡池',notice:'公告',release:'版本',rules:'区服规则',maintenance:'公告与版本',realm:'区服',state:'存档'};
let token='', operations={}, current='', busy=false, generation=0;
async function request(path, method='GET', data){
  const options={method,cache:'no-store',credentials:'omit',headers:{Authorization:'Bearer '+token}};
  if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
  const response=await fetch(path,options), body=await response.json();
  if(!response.ok||!body.ok)throw Error(body.error?.message||('HTTP '+response.status));
  return body.result;
}
function status(text){el('status').textContent=text;}
function renderList(){
  const term=el('filter').value.trim().toLowerCase(), nav=el('operations'); nav.replaceChildren();
  for(const name of Object.keys(operations).sort()){
    const group=labels[name.split('.')[0]]||'';
    if(!(name+' '+group+' '+operations[name].description).toLowerCase().includes(term))continue;
    const button=document.createElement('button');button.type='button';button.textContent=name;
    button.setAttribute('aria-current',String(name===current)); button.onclick=()=>select(name);nav.append(button);
  }
}
function select(name){
  current=name;const op=operations[name], schema=op.inputSchema, fields=el('fields');fields.replaceChildren();
  el('title').textContent=(labels[name.split('.')[0]]||'管理操作')+' / '+name;
  el('description').textContent=op.description;
  for(const [key,spec] of Object.entries(schema.properties||{})){
    const required=schema.required?.includes(key), type=Array.isArray(spec.type)?spec.type[0]:spec.type;
    const label=document.createElement('label');label.htmlFor='field-'+key;label.textContent=key+(required?' *':'');
    let input;
    if(type==='boolean'||spec.enum){
      input=document.createElement('select'); input.add(new Option('请选择',''));
      for(const value of (spec.enum||[true,false]))input.add(new Option(String(value),String(value)));
    }else if(type==='object'||type==='array'){
      input=document.createElement('textarea');input.rows=4;input.placeholder=type==='array'?'[]':'{}';
    }else{
      input=document.createElement('input');input.type=type==='integer'||type==='number'?'number':'text';
      if(spec.minimum!==undefined)input.min=spec.minimum;if(spec.maximum!==undefined)input.max=spec.maximum;
      if(type==='integer')input.step='1';
    }
    input.dataset.type=type;input.name=key;input.required=!!required;input.id='field-'+key;fields.append(label,input);
  }
  el('schema').textContent=JSON.stringify(schema,null,2);el('result').textContent='';
  el('execute').textContent=op.readOnly?'查询':op.availability==='stopped'?'需停服后通过 CLI 执行':'执行';
  el('execute').disabled=busy||op.availability==='stopped';renderList();
}
el('login').onsubmit=async event=>{
  event.preventDefault(); const attempt=++generation; token=el('token').value;status('');
  try{
    const spec=await request('/admin/v1/schema');if(attempt!==generation)return;
    operations=spec.operations;el('token').value='';el('login').hidden=true;el('workspace').hidden=false;
    select(operations['realm.status']?'realm.status':Object.keys(operations)[0]);
  }catch(error){if(attempt===generation){token='';status(error.message);}}
};
el('logout').onclick=()=>{
  ++generation;token='';operations={};current='';busy=false;el('result').textContent='';el('fields').replaceChildren();
  el('workspace').hidden=true;el('login').hidden=false;status('');el('token').focus();
};
el('operation').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  const name=current,epoch=generation,op=operations[name],data={};
  try{
    for(const input of el('fields').querySelectorAll('[name]')){
      if(input.value==='')continue;
      data[input.name]=input.dataset.type==='string'?input.value:JSON.parse(input.value);
    }
    if(!op.readOnly&&!window.confirm('在当前区服执行 '+name+'？'))return;
    busy=true;el('execute').disabled=true;status('');
    const query=op.readOnly?new URLSearchParams(Object.entries(data)).toString():'';
    const url='/admin/v1/operations/'+name+(query?'?'+query:'');
    const result=await request(url,op.readOnly?'GET':'POST',op.readOnly?undefined:data);
    if(epoch===generation&&name===current)el('result').textContent=JSON.stringify(result,null,2);
  }catch(error){if(epoch===generation)status(error.message);}
  finally{if(epoch===generation){busy=false;el('execute').disabled=operations[current].availability==='stopped';}}
};
el('filter').oninput=renderList;
"""

ASSETS = {"/admin/": ("text/html", HTML.encode("utf-8")),
          "/admin/console.css": ("text/css", CSS.encode("utf-8")),
          "/admin/console.js": ("text/javascript", JS.encode("utf-8"))}
