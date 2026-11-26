"""Player-initiated calls to the selected private realm. All form data arrives on standard input."""
import base64
import contextlib
import http.client
import hashlib
import tempfile
import zipfile
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
            "challenge":"/local/v1/accounts/challenge","lookup":"/local/v1/accounts/lookup",
            "bind":"/local/v1/accounts/bind","maintenance":"/private/maintenance",
            "gifts":"/private/gifts","claim-gift":"/local/v1/accounts/claim-gift"}


IMPORT_OPS = ("import-preview","import-apply","import-undo")
OPERATIONS["change-password"] = "/local/v1/accounts/change-password"
OPERATIONS.update({key:"/local/v1/accounts/"+key for key in IMPORT_OPS})
IMPORT_TICKET = re.compile(r"ofi_[A-Za-z0-9_-]{43}",re.ASCII)
PREVIEW_ID = re.compile(r"ofv_[a-f0-9]{32}",re.ASCII)


def import_receipt(doc):
    ticket, ttl = doc.get("import_ticket"), doc.get("import_expires_in")
    if not isinstance(ticket,str) or not IMPORT_TICKET.fullmatch(ticket) or type(ttl) is not int or not 0<ttl<=900:
        raise ValueError("私服导入会话格式无效。")
    return {"import_ticket":ticket,"import_expires_in":ttl}


def identity_choice(choice):
    if not isinstance(choice,dict) or set(choice)-{"uid_mode","nickname_mode","nickname"}:
        return None
    uid_mode=choice.get("uid_mode")
    name_mode=choice.get("nickname_mode")
    if uid_mode not in ("original","new") or name_mode not in ("original","custom","local"):
        return None
    clean={"uid_mode":uid_mode,"nickname_mode":name_mode}
    if name_mode=="custom":
        name=choice.get("nickname")
        if not isinstance(name,str) or not 1<=len(name.strip())<=32 or any(not c.isprintable() for c in name):
            return None
        clean["nickname"]=name.strip()
    return clean


def returned_identity(info,preview):
    if not isinstance(info,dict):
        raise ValueError("区服返回的角色身份格式无效。")
    uid=info.get("uid")
    nickname=info.get("nickname")
    if uid is not None and (type(uid) is not int or not 0<=uid<=0xffffffff):
        raise ValueError("区服返回的角色 UID 格式无效。")
    if not isinstance(nickname,str) or len(nickname)>128 or not all(c.isprintable() for c in nickname):
        raise ValueError("区服返回的角色名格式无效。")
    clean={"uid":uid,"nickname":nickname}
    if preview:
        for field in ("uid_status","nickname_status"):
            if info.get(field) not in ("available","taken","invalid","missing"):
                raise ValueError("区服返回的角色名或 UID 状态无效。")
            clean[field]=info[field]
    return clean


def import_backup(action, inp, config):
    ticket=inp.get("import_ticket")
    if not isinstance(ticket,str) or not IMPORT_TICKET.fullmatch(ticket):
        return {"ok":False,"code":"import_login_required"}
    form={"import_ticket":ticket}
    compatibility=[]
    omitted={}
    if action=="import-preview":
        from save_import_data import BackupFormatError,project_file
        try:
            form["data"]=project_file(Path(inp["path"]))
        except BackupFormatError as err:
            return {"ok":False,"code":"invalid_backup","message":str(err)}
        except (OSError,KeyError,ValueError,TypeError,RuntimeError,zipfile.BadZipFile):
            return {"ok":False,"code":"invalid_backup","message":"无法读取全进度备份。请选择结果 ZIP、snapshot.json 或结果目录。"}
        data=form["data"]
        if "profile_sign" in data or "pets" in data or "field_progress" in data or "fishing_rods" in data or "badge_tree" in data or "uid" in data.get("identity", {}):
            status,region=request(config,"GET","/local/v1/region",max_bytes=32768)
            if status!=200 or region.get("realm")!=PRIVATE_REALM:
                return {"ok":False,"code":"import_capabilities_unavailable","message":"无法读取所选区服的存档导入能力，请重新测试连接。"}
            features=region.get("save_import_features",[])
            if "badge_tree" in data and "badge_tree" not in features:
                tree=data.pop("badge_tree")
                omitted["旧服跳过的星云树记录"]=sum(len(nodes)+int(taken) for _,nodes,taken in tree["pages"])+len(tree["claimed"])+len(tree["achievements"])
                compatibility.append("该区服版本无法继承备份中的星云树节点、页面领奖和成就记录。")
            if not isinstance(features,list) or any(not isinstance(feature,str) for feature in features):
                return {"ok":False,"code":"import_capabilities_unavailable","message":"所选区服的存档导入能力格式无效，请联系服主。"}
            identity = data.get("identity", {})
            if "uid" in identity and "public_uid_label" in features:
                data["identity"] = dict(identity)
                data["identity"]["public_label"] = data["identity"].pop("uid")
            if "profile_sign" in data and "profile_sign" not in features:
                data.pop("profile_sign")
                omitted["旧服跳过的个人签名"]=1
                compatibility.append("该区服版本会跳过个人签名及其可见性。")
            if "fishing_rods" in data and "fishing_rods" not in features:
                omitted["旧服跳过的鱼竿装备槽"]=len(data.pop("fishing_rods"))
                compatibility.append("此区服可继承背包中的鱼竿，装备选择需进游戏后重新设置。")
            if "pets" in data and "pets" not in features:
                pets=data.pop("pets")
                omitted["旧服跳过的宠物明细"]=len(pets["entries"])
                compatibility.append(f"该区服版本无法继承宠物。本备份记录 {pets['total']} 只、含 {len(pets['entries'])} 只明细，本次导入会跳过宠物及仓库容量。")
            if "field_progress" in data and "field_progress" not in features:
                field=data.pop("field_progress")
                categories=(("devices","区域装置"),("riddles","谜题"),("challenges","挑战"),("collectibles","收藏点"),("goals","区域目标"))
                omitted["旧服跳过的区域进度"]=sum(len(field.get(k,())) for k,_ in categories)
                detail="、".join(f"{label} {len(field[key])} 条" for key,label in categories if field.get(key))
                compatibility.append("该区服版本会跳过"+(detail or "备份中的区域进度")+"。")
            if compatibility:
                compatibility.append("要保留上述进度，请先取消，待服主更新后重新预览。确认导入将仅恢复本页列出的项目。")
    elif action=="import-apply":
        preview=inp.get("preview_id")
        if not isinstance(preview,str) or not PREVIEW_ID.fullmatch(preview):
            return {"ok":False,"code":"invalid_preview"}
        form["preview_id"]=preview
        if "identity" in inp:
            selected=identity_choice(inp["identity"])
            if selected is None:
                return {"ok":False,"code":"invalid_import_identity"}
            form["identity"]=selected
    if len(json.dumps(form,ensure_ascii=False,separators=(",",":")).encode("utf8"))>2*1024*1024:
        return {"ok":False,"code":"backup_too_large","message":"备份中的可导入数据超过区服单次上限。"}
    status,doc=request(config,"POST",OPERATIONS[action],form,max_bytes=32768)
    if status!=200 or doc.get("ok") is not True:
        return refusal(status,doc)
    counts=doc.get("counts")
    if (not isinstance(counts,dict) or len(counts)>100 or
        any(not isinstance(k,str) or not 1<=len(k)<=72 or type(v) is not int or not 0<=v<=2**31 for k,v in counts.items())):
        raise ValueError("私服导入统计格式无效。")
    result={"ok":True,"counts":{**omitted,**counts}}
    if "identity" in doc:
        result["identity"]=returned_identity(doc["identity"],action=="import-preview")
    if action=="import-preview":
        eligible, preview, warnings=doc.get("eligible"),doc.get("preview_id"),doc.get("warnings",[])
        if (type(eligible) is not bool or eligible and (not isinstance(preview,str) or not PREVIEW_ID.fullmatch(preview)) or
            not isinstance(warnings,list) or len(warnings)>48 or any(not isinstance(w,str) or len(w)>400 for w in warnings)):
            raise ValueError("私服导入预览格式无效。")
        result.update(eligible=eligible,preview_id=preview if eligible else "",warnings=compatibility+warnings)
    return result


def gift_token(value):
    return isinstance(value,str) and re.fullmatch(r"ofg_[A-Za-z0-9_-]{43}",value,re.ASCII)


def gift_receipt(doc):
    token,ttl=doc.get("gift_ticket"),doc.get("gift_expires_in")
    if not gift_token(token) or type(ttl) is not int or not 0<=ttl<=3600:
        raise ValueError("私服礼包服务返回的数据格式不匹配。")
    return {"gift_ticket":token,"gift_expires_in":ttl}


def refusal(code,doc):
    error=doc.get("code") or doc.get("error")
    if not isinstance(error,str) or not re.fullmatch(r"[a-z0-9_]{1,60}",error):
        error="private_server_refused"
    return {"ok":False,"code":error,"status":code}


def valid_local(host):
    if host=="localhost":return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


@contextlib.contextmanager
def connection(config):
    if config.get("publicTls"):
        from player_tls_channels import PlayerTlsChannels
        channels = PlayerTlsChannels(config["publicTls"], config.get("caFile"))
        with channels.open(18881) as stream:
            client = http.client.HTTPConnection("127.0.0.1", 18881, timeout=12)
            client.sock = stream
            try:
                yield client
            finally:
                client.close()
        return
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
    if action in ("challenge","lookup"):
        if "captcha_lookup_v1" not in (server.get("account_features") or []):
            return {"ok":False,"code":"unsupported_lookup","message":"这个版本的区服支持直接登录，暂不支持检查账号。"}
        keys = ("username","purpose") if action=="challenge" else ("username","challenge_id","answer")
        status, doc = request(config,"POST",OPERATIONS[action],{k:inp.get(k,"") for k in keys},max_bytes=131072)
        if status!=200:
            return refusal(status,doc)
        if action=="challenge":
            challenge_id, image = doc.get("challenge_id"), doc.get("image_png")
            if not isinstance(challenge_id,str) or not 1<=len(challenge_id)<=256 or not isinstance(image,str) or len(image)>120000:
                raise ValueError("私服验证码格式无效。")
            png=base64.b64decode(image,validate=True)
            if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png)<33 or not (0<int.from_bytes(png[16:20],"big")<=1024 and 0<int.from_bytes(png[20:24],"big")<=256):
                raise ValueError("私服验证码图片格式无效。")
            return {"ok":True,"challenge_id":challenge_id,"image_png":image}
        if type(doc.get("exists")) is not bool or not isinstance(doc.get("account_proof"),str) or not 1<=len(doc["account_proof"])<=512:
            raise ValueError("私服账号查询结果无效。")
        return {"ok":True,"exists":doc["exists"],"account_proof":doc["account_proof"]}
    if action=="change-password":
        if "password_change_v1" not in (server.get("account_features") or []):
            return {"ok":False,"code":"unsupported_password_change","message":"此区服尚未支持玩家修改密码，请联系管理员。"}
        form={k:inp[k] for k in ("username","password","new_password","challenge_id","answer") if k in inp}
        status,doc=request(config,"POST",OPERATIONS[action],form)
        if status!=200:return refusal(status,doc)
        if doc.get("changed") is not True or doc.get("reauthenticate") is not True:
            raise ValueError("区服改密回执格式不正确，请重新登录。")
        return {"ok":True}
    if action in IMPORT_OPS:
        return import_backup(action,inp,config)
    if action=="gifts":
        code,doc=request(config,"GET",OPERATIONS[action],max_bytes=524288)
        if code!=200:return refusal(code,doc)
        rows=doc.get("gifts")
        if doc.get("ok") is not True or not isinstance(rows,list) or len(rows)>2400:
            raise ValueError("私服礼包目录格式无效。")
        for row in rows:
            if (not isinstance(row,dict) or
                any(type(row.get(k)) is not int or not 0<=row[k]<=0xffffffff for k in ("shop","grid","pool")) or
                any(not isinstance(row.get(k),str) or not 0<len(row[k])<=256 for k in ("title","section"))):
                raise ValueError("私服礼包目录格式无效。")
        return {"ok":True,"gifts":rows}
    if action=="claim-gift":
        if not gift_token(inp.get("ticket")):return {"ok":False,"code":"gift_login_required"}
        form={k:inp[k] for k in ("ticket","shop","grid","pool","request_id") if k in inp}
        code,doc=request(config,"POST",OPERATIONS[action],form)
        if code not in (200,201):return refusal(code,doc)
        if doc.get("ok") is not True or doc.get("claimed") is not True:
            raise ValueError("私服礼包服务未确认到账。")
        return {"ok":True,"claimed":True,**gift_receipt(doc)}
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
    if action in ("register","login"):
        form.update({k:inp[k] for k in ("challenge_id","answer","account_proof") if inp.get(k)})
    if action in ("register","bind"):
        form["nickname"]=inp.get("nickname","")
    if action=="bind":form.update(proof(inp.get("profile","")))
    code,doc=request(config,"POST",OPERATIONS[action],form)
    if code not in (200,201):
        return refusal(code,doc)
    if (doc.get("realm")!=PRIVATE_REALM or not re.fullmatch(r"ofp_[a-f0-9]{32}",str(doc.get("slot",""))) or
        not isinstance(doc.get("ticket"),str) or not doc["ticket"] or not isinstance(doc.get("nickname"),str)):
        raise ValueError("私服账号服务返回的数据格式不匹配。")
    # Only the launching WinForms process gets this receipt over anonymous pipes.
    # The private server, not the channel SDK, may attach it to the exact game PID.
    return {"ok":True,"nickname":doc["nickname"],"slot":doc["slot"],"ticket":doc["ticket"],
            "expires_in":doc.get("expires_in",0),**(gift_receipt(doc) if "gift_ticket" in doc else {}),
            **(import_receipt(doc) if "import_ticket" in doc else {})}


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
