"""Player-initiated calls to the selected private realm. All form data arrives on standard input."""
import base64
import contextlib
import http.client
import hashlib
import tempfile
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parent/"OverField-Server"/"app"))
from native_endpoint import validate_endpoint
from realm_addresses import ssh_address
from ssh_game_channel import SshChannels
from notice_images import HEX, IMAGE_PATH, read_response
from realm_maintenance import MAX_FEED, checked_feed

PRIVATE_REALM="overfield-local-v1"
OPERATIONS={"register":"/local/v1/accounts/register","login":"/local/v1/accounts/login",
            "bind":"/local/v1/accounts/bind","maintenance":"/private/maintenance"}


def valid_local(host):
    if host=="localhost":return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


@contextlib.contextmanager
def connection(config):
    target=config.get("ssh") or ""
    if config.get("viaSsh") and not target:
        raise ValueError("请选择远端 SSH 目标后再连接私服。")
    if target:
        target,port=ssh_address(target,config.get("sshPort"))
        channels=SshChannels(target,None if port is None else str(port),os.environ.get("OF_REMOTE_SSH_CONFIG"))
        with channels.open(18881) as stream:
            client=http.client.HTTPConnection("127.0.0.1",18881,timeout=12)
            client.sock=stream
            try:yield client
            finally:client.close()
    else:
        url=urlsplit(validate_endpoint(config["url"]))
        if not valid_local(url.hostname):
            raise ValueError("请选择本机回环网关或经主机密钥校验的 SSH 服务器。")
        if url.scheme=="https":
            client=http.client.HTTPSConnection(url.hostname,url.port,timeout=10,context=ssl.create_default_context())
        else:
            client=http.client.HTTPConnection(url.hostname,url.port,timeout=10)
        try:yield client
        finally:client.close()


def request(config,method,path,form=None,max_bytes=65536):
    body=None if form is None else json.dumps(form,ensure_ascii=False,separators=(",",":")).encode("utf8")
    with connection(config) as client:
        client.request(method,path,body=body,headers={"Content-Type":"application/json","Connection":"close"})
        response=client.getresponse()
        raw=response.read(max_bytes+1)
        if len(raw)>max_bytes:raise ValueError("私服返回的内容过大。")
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError("当前地址未返回有效的私服数据。")
        return response.status,result


def proof(path):
    path=Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size>16384:
        raise ValueError("请选择由该私服导出的角色档案 JSON。")
    doc=json.loads(path.read_bytes())
    if (not isinstance(doc,dict) or doc.get("realm")!=PRIVATE_REALM or
        not re.fullmatch(r"ofp_[a-f0-9]{32}",str(doc.get("slot",""))) or
        not isinstance(doc.get("profile_key"),str) or not 32<=len(doc["profile_key"])<=256):
        raise ValueError("绑定需要此私服导出的 slot 与 profile_key 所有权凭证。")
    return {"slot":doc["slot"],"profile_key":doc["profile_key"]}


def download(config, expected, path):
    status,meta=request(config,"GET",OPERATIONS["maintenance"],max_bytes=MAX_FEED)
    release=meta.get("release")
    if status!=200 or not isinstance(release,dict) or release.get("sha256")!=expected:
        return {"ok":False,"code":"release_changed"}
    digest=release.get("sha256","")
    if not re.fullmatch(r"[a-f0-9]{64}",digest) or release.get("url")!="/private/releases/"+digest:
        return {"ok":False,"code":"untrusted_release"}
    size=release.get("size")
    if type(size) is not int or not 0<size<=4*1024**3:
        return {"ok":False,"code":"untrusted_release"}
    target=Path(path)
    if not target.is_absolute() or not target.parent.is_dir() or target.exists() or target.is_symlink():
        return {"ok":False,"code":"need_new_file"}
    temp=None
    try:
        with connection(config) as client:
            client.request("GET",release["url"],headers={"Connection":"close"})
            reply=client.getresponse()
            if reply.status!=200 or reply.getheader("Content-Length")!=str(size):
                return {"ok":False,"code":"release_integrity"}
            h=hashlib.sha256();count=0
            with tempfile.NamedTemporaryFile("wb",dir=target.parent,prefix=".of-release-",delete=False) as out:
                temp=Path(out.name)
                while count<size:
                    data=reply.read(min(1024*1024,size-count))
                    if not data:break
                    out.write(data);h.update(data);count+=len(data)
            if count!=size or h.hexdigest()!=digest:
                return {"ok":False,"code":"release_integrity"}
        if os.name=="nt":os.rename(temp,target)
        else:os.link(temp,target)
        return {"ok":True,"downloaded":True}
    finally:
        if temp is not None:temp.unlink(missing_ok=True)


def handle(inp):
    action=inp["action"]
    if action not in OPERATIONS and action not in ("download","notice-image"):raise ValueError("未知私服操作。")
    config=inp["server"]
    if not isinstance(config,dict):raise ValueError("缺少私服连接设置。")
    try:
        status,server=request(config,"GET","/local/v1/region")
    except (ConnectionError,TimeoutError):
        if config.get("viaSsh") or config.get("ssh") or not config.get("localServer"):raise
        from native_launch import changing,local_start,is_running
        from prepare_client import prepare
        home=HERE.parent
        with changing(home):
            if is_running(home):raise ValueError("当前地址未返回有效的私服数据。")
            # A managed local realm requires the same verified original resource store as gameplay.
            prepare(Path(config["source"]),home,validate_endpoint(config["url"]),progress=lambda *a,**k:None)
            local_start(home,home/"OverField-Server",home/"Client",config["url"],{})
        status,server=request(config,"GET","/local/v1/region")
    if status!=200 or server.get("realm")!=PRIVATE_REALM:
        raise ValueError("所选地址未提供兼容的私服账号服务。")
    if action=="download":
        return download(config,inp.get("sha256"),inp.get("destination",""))
    if action=="maintenance":
        status,doc=request(config,"GET",OPERATIONS[action],max_bytes=MAX_FEED)
        if status!=200:return {"ok":False,"code":"maintenance_unavailable"}
        return {"ok":True,"maintenance":checked_feed(doc)}
    if action=="notice-image":
        digest=inp.get("sha256")
        if not isinstance(digest,str) or not HEX.fullmatch(digest):
            raise ValueError("私服公告图片标识无效。")
        with connection(config) as client:
            client.request("GET",IMAGE_PATH+digest,headers={"Connection":"close"})
            desc,data=read_response(client.getresponse(),digest)
        return {"ok":True,"image":base64.b64encode(data).decode("ascii")}
    form={"username":inp.get("username",""),"password":inp.get("password","")}
    if action in ("register","bind"):
        form["nickname"]=inp.get("nickname","")
    if action=="bind":form.update(proof(inp.get("profile","")))
    code,doc=request(config,"POST",OPERATIONS[action],form)
    if code not in (200,201):
        error=doc.get("code") or doc.get("error")
        if not isinstance(error,str) or not re.fullmatch(r"[a-z0-9_]{1,60}",error):error="private_server_refused"
        return {"ok":False,"code":error,"status":code}
    if (doc.get("realm")!=PRIVATE_REALM or not re.fullmatch(r"ofp_[a-f0-9]{32}",str(doc.get("slot",""))) or
        not isinstance(doc.get("ticket"),str) or not doc["ticket"] or not isinstance(doc.get("nickname"),str)):
        raise ValueError("私服账号服务返回的数据格式不匹配。")
    # Only the launching WinForms process gets this receipt over anonymous pipes.
    # The private server, not the channel SDK, may attach it to the exact game PID.
    return {"ok":True,"nickname":doc["nickname"],"slot":doc["slot"],"ticket":doc["ticket"],
            "expires_in":doc.get("expires_in",0)}


if __name__=="__main__":
    try:
        line=sys.stdin.buffer.read(32769)
        if len(line)>32768:raise ValueError("请求太大。")
        output=handle(json.loads(line))
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,http.client.HTTPException) as exc:
        # Never repeat raw protocol bodies, filenames, usernames, passwords, or tickets.
        if isinstance(exc,(ConnectionError,TimeoutError)):
            message="连接私服失败，请检查服务是否已启动、地址或 SSH 主机密钥。"
        elif isinstance(exc,ssl.SSLError):
            message="未能验证私服连接的证书。"
        elif type(exc) is ValueError and str(exc).startswith(("请选择","绑定需要","私服","所选","当前地址","SSH")):
            message=str(exc)
        else:message="私服请求未完成，请检查连接配置和服务版本。"
        output={"ok":False,"code":"transport","message":message}
    print(json.dumps(output,ensure_ascii=True,separators=(",",":")))

