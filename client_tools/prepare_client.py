"""Prepare and update a private copy of the retail Unity player without touching its installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from native_endpoint import KNOWN_SOURCE, export_config, validate_endpoint

LAUNCHER = "a3702251bd92b7ba773b00eeffef7a63a311098e96e2e83f541e7ea78070001b"
ASSEMBLY = "2db98364241c95864781f77877523b9d2f728b22a91935fa30cdfb33a8b7f4ea"
BUILD = "2026-10-21-14-05-50"
PATCH = "2026-11-07-22-39-35"
RELEASE_ROOTS = {"launcher.exe","GameAssembly.dll","UnityPlayer.dll","baselib.dll","UnityCrashHandler64.exe","version"}
RELEASE_DIRS = {"il2cpp_data","Plugins","Resources","StreamingAssets","yoo"}
RELEASE_FILES = {"boot.config","ScriptingAssemblies.json","RuntimeInitializeOnLoads.json"}

def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def save(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write((json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
        except BaseException:
            f.close();tmp.unlink(missing_ok=True);raise
    try:
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)

def channel_runtime_state(relative):
    parts=Path(relative).as_posix().lower().split("/")
    return (parts[:4]==["launcher_data","plugins","win","pcsdk_release"] and
            len(parts)>4 and parts[4] in ("cache","logs","logs.txt"))

def asset_paths(src):
    dd=src/"launcher_Data"
    for item in sorted(RELEASE_ROOTS):
        yield src/item
    for item in sorted(RELEASE_FILES):
        yield dd/item
    for directory in sorted(RELEASE_DIRS):
        root=dd/directory
        if not root.is_dir():
            raise ValueError("游戏目录不完整，缺少 "+directory)
        for parent, dirs, files in os.walk(root,followlinks=False):
            base=Path(parent)
            dirs[:]=[d for d in dirs if not channel_runtime_state((base/d).relative_to(src))]
            for entry in dirs:
                candidate=base/entry
                if candidate.is_symlink() or getattr(candidate,'is_junction',lambda:False)():
                    raise ValueError("游戏资源树含目录链接，请选择实体安装。")
            for f in files:
                p=base/f
                if channel_runtime_state(p.relative_to(src)):continue
                if p.suffix.lower() in ('.log','.dmp','.tmp','.pcap'):continue
                if p.is_symlink():raise ValueError("游戏资源含文件链接。")
                yield p

def _from_installed(source, home, endpoint, progress):
    src=Path(source).resolve()
    home=Path(home).resolve()
    dest=home/"Client"
    endpoint=validate_endpoint(endpoint)
    if home.is_relative_to(src) or (src.is_relative_to(home) and src!=dest):
        raise ValueError("请选择与原游戏分开的联网私服工具目录。")
    home.mkdir(parents=True,exist_ok=True)
    meta_path=home/"client-config.json"
    old=json.loads(meta_path.read_bytes()) if meta_path.is_file() else {}
    baseline=home/"ClientSettings"/"data.unity3d"
    original=src/"launcher_Data/data.unity3d"
    def settings_source():
        if baseline.exists():
            if sha(baseline)!=KNOWN_SOURCE:raise ValueError("独立原始配置文件校验失败。")
            return baseline
        if not original.is_file() or sha(original)!=KNOWN_SOURCE:
            raise ValueError("更改区服需要此版本原始 data.unity3d，请选择原版安装目录。")
        baseline.parent.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=baseline.parent,delete=False) as pending:
            temp=Path(pending.name)
        try:
            shutil.copyfile(original,temp)
            if sha(temp)!=KNOWN_SOURCE:raise ValueError("复制中的原始配置发生变化。")
            os.replace(temp,baseline)
        finally:
            temp.unlink(missing_ok=True)
        return baseline
    if dest.exists():
        if (not old or not (dest/".of-private-client.json").is_file()
            or json.loads((dest/".of-private-client.json").read_bytes()).get("instance")!=old.get("instance")):
            raise ValueError("现有 Client 目录不属于这份配置，请为此客户端选择独立位置。")
        if sha(dest/"launcher.exe")!=LAUNCHER or sha(dest/"GameAssembly.dll")!=ASSEMBLY:
            raise ValueError("私服副本的原版程序已经变更。")
        app_info=dest/"launcher_Data/app.info"
        app_bytes=app_info.read_bytes()
        expected_info="OFPriv\n开放空间".encode("utf8")
        if app_bytes!=expected_info:
            if app_bytes!="OFPriv\r\n开放空间\r\n".encode("utf8"):
                raise ValueError("私服副本的 app.info 已由其他程序修改。")
            replacement=app_info.with_name(".app.info.new")
            replacement.write_bytes(expected_info)
            os.replace(replacement,app_info)
        package=dest/"launcher_Data/data.unity3d"
        checksum=sha(package)
        pending=home/".client-route.pending.json"
        stage=home/"ClientSettingsPending"/"next-route.unity3d"
        if pending.exists():
            update=json.loads(pending.read_bytes())
            if update["instance"]!=old["instance"]:
                raise ValueError("区服配置变更记录不匹配。")
            if checksum==update["sha256"]:
                old=update
                save(meta_path,old)
            elif checksum!=old["sha256"]:
                raise ValueError("区服配置在写入过程中被其他程序更改。")
            pending.unlink()
        if checksum!=old.get("sha256"):
            raise ValueError("私服副本的配置已由其他程序修改。")
        if endpoint!=old.get("url") or old.get("layout")!=2:
            baseline_file=settings_source()
            staged_hash=sha(stage) if stage.is_file() else None
            result=export_config(baseline_file,stage,endpoint,previous_sha256=staged_hash)
            updated=dict(old,url=endpoint,sha256=result["sha256"],layout=2)
            save(pending,updated)
            os.replace(stage,package)
            save(meta_path,updated)
            pending.unlink()
            old=updated
        elif not baseline.is_file() and original.is_file():
            settings_source()
        if not old.get("freshSdkCache"):
            # Early private test copies included a webview cache from the source install.
            # Separate it on the first update. Preserve this copy in the user's private state.
            sdk=dest/"launcher_Data/Plugins/Win/PCSDK_release"
            reserved=home/"State/previous-channel-cache"
            for name in ("cache","logs","logs.txt"):
                item=sdk/name
                if item.exists():
                    reserved.mkdir(parents=True,exist_ok=True)
                    if (reserved/name).exists():
                        raise ValueError("私服缓存迁移目标已存在，请先检查 State/previous-channel-cache。")
                    os.replace(item,reserved/name)
            old["freshSdkCache"]=True
            save(meta_path,old)
        return old
    if (src==dest or not (src/"version").is_file() or
        (src/"version").read_text(encoding="utf-8-sig").strip()!=BUILD or
        (src/"launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version").read_text(encoding="utf-8-sig").strip()!=PATCH or
        sha(src/"launcher.exe")!=LAUNCHER or sha(src/"GameAssembly.dll")!=ASSEMBLY):
        raise ValueError("需要 TapTap 国服 PC 1.1.12 的原版程序与 11 月 7 日资源。")
    original=settings_source()
    partial=home/".client-preparing"
    owner=partial/".of-private-client.json"
    if partial.exists():
        if not owner.is_file() or json.loads(owner.read_bytes()).get("source")!=str(src):
            raise ValueError("暂存目录已存在并属于其他安装。")
        meta=json.loads(owner.read_bytes())
    else:
        partial.mkdir()
        meta={"format":1,"instance":uuid4().hex,"source":str(src)}
        save(owner,meta)
    inputs=list(asset_paths(src))
    shared=copied=0
    built=Path("launcher_Data/StreamingAssets/yoo/DefaultPackage")
    for index,p in enumerate(inputs):
        if index%1000==0:progress("正在准备原版资源 %d / %d"%(index,len(inputs)),flush=True)
        rel=p.relative_to(src);out=partial/rel
        out.parent.mkdir(parents=True,exist_ok=True)
        if out.is_symlink():raise ValueError("暂存文件不能是符号链接。")
        length=p.stat().st_size
        if out.exists() and out.stat().st_size==length:
            if os.path.samefile(p,out):shared+=length
            else:copied+=length
            continue
        if rel.parent==built and re.fullmatch(r"[a-f0-9]{32}\.bundle",p.name):
            try:
                os.link(p,out);shared+=length;continue
            except OSError:
                pass
        # Cache, executables, settings and DLLs are independent files.
        tmp=out.with_name(out.name+".of-partial")
        try:
            shutil.copyfile(p,tmp)
            if tmp.stat().st_size!=length:raise OSError("复制中的资源发生变化")
            os.replace(tmp,out);copied+=length
        finally:
            tmp.unlink(missing_ok=True)
    dd=partial/"launcher_Data"
    # app.info and the Unity PlayerSettings use the same private company for local saves.
    (dd/"app.info").write_bytes("OFPriv\n开放空间".encode("utf8"))
    (dd/"Quicksdk").mkdir(exist_ok=True)
    cfg=dd/"data.unity3d"
    if cfg.exists():
        if meta.get('sha256')!=sha(cfg):raise ValueError("暂存的原版派发配置发生变化。")
        result=export_config(original,cfg,endpoint,previous_sha256=meta['sha256'])
    else:
        result=export_config(original,cfg,endpoint)
    meta.update(layout=2,freshSdkCache=True,sha256=result["sha256"],url=endpoint,sharedBytes=shared,privateBytes=copied+cfg.stat().st_size)
    save(owner,meta)
    # If a previous process ended in between these writes the prepared directory still has its ownership marker.
    save(meta_path,meta)
    os.rename(partial,dest)
    return meta


def _media_entry(name):
    from pathlib import PurePosixPath
    rel=PurePosixPath(name)
    if rel.is_absolute() or "\\" in name or ":" in name or ".." in rel.parts or rel.as_posix()!=name:
        raise ValueError("安装媒体中有无效的资源路径。")
    parts=rel.parts
    if channel_runtime_state(rel.as_posix()):return False
    if len(parts)==1:
        return name in RELEASE_ROOTS
    if len(parts)==2:
        return parts[0]=="launcher_Data" and parts[1] in RELEASE_FILES
    return (parts[0]=="launcher_Data" and parts[1] in RELEASE_DIRS
            and rel.suffix.lower() not in (".log",".dmp",".tmp",".pcap"))


def _install_media(media,home,endpoint,progress):
    from client_media import open_media
    import binascii
    import stat
    endpoint=validate_endpoint(endpoint)
    partial=home/".client-preparing"
    owner=partial/".of-private-client.json"
    home.mkdir(parents=True,exist_ok=True)
    if partial.exists():
        if not owner.is_file():
            raise ValueError("现有暂存目录未标记为本次安装。")
        meta=json.loads(owner.read_bytes())
        if meta.get("source")!=str(media):
            raise ValueError("暂存目录的原版资源来源不同。")
    else:
        partial.mkdir()
        meta={"format":1,"instance":uuid4().hex,"source":str(media)}
        save(owner,meta)
    progress("正在校验原版安装媒体（约 25.8 GiB）。",flush=True)
    with open_media(media) as archive:
        if archive.read("version").decode().strip()!=BUILD or archive.read(
           "launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version").decode().strip()!=PATCH:
            raise ValueError("安装媒体中游戏版本不正确。")
        items=[item for item in archive.infolist() if not item.is_dir() and _media_entry(item.filename)]
        copied=sum(i.file_size for i in items)
        remaining=sum(i.file_size for i in items if not (partial/i.filename).is_file())
        if shutil.disk_usage(home).free<remaining+250_000_000:
            raise ValueError("从安装媒体导入时，目标盘需要约 27 GiB 的可用空间。")
        def extract(item,output):
            if stat.S_ISLNK(item.external_attr>>16) or not output.resolve().is_relative_to(home):
                raise ValueError("安装媒体含非普通资源。")
            output.parent.mkdir(parents=True,exist_ok=True)
            if output.is_file() and output.stat().st_size==item.file_size:
                crc=0
                with output.open("rb") as existing:
                    for block in iter(lambda:existing.read(1024*1024),b""):
                        crc=binascii.crc32(block,crc)
                if crc==item.CRC:
                    return
            fd,tmp=tempfile.mkstemp(dir=output.parent,prefix=".of-media-")
            try:
                with os.fdopen(fd,"wb") as dst,archive.open(item) as src:
                    shutil.copyfileobj(src,dst,1024*1024)
                os.replace(tmp,output)
            finally:
                Path(tmp).unlink(missing_ok=True)
        baseline=home/"ClientSettings/data.unity3d"
        extract(archive.getinfo("launcher_Data/data.unity3d"),baseline)
        if sha(baseline)!=KNOWN_SOURCE:
            raise ValueError("安装媒体中的 Unity 配置文件校验失败。")
        for index,item in enumerate(items):
            if index%500==0:
                progress("正在从原版媒体安装游戏 %d / %d"%(index,len(items)),flush=True)
            extract(item,partial/item.filename)
    if sha(partial/"launcher.exe")!=LAUNCHER or sha(partial/"GameAssembly.dll")!=ASSEMBLY:
        raise ValueError("安装媒体中的 Unity 程序校验失败。")
    dd=partial/"launcher_Data"
    (dd/"app.info").write_bytes("OFPriv\n开放空间".encode("utf8"))
    (dd/"Quicksdk").mkdir(exist_ok=True)
    cfg=dd/"data.unity3d"
    if cfg.exists() and meta.get("sha256")!=sha(cfg):
        raise ValueError("安装暂存中的 Unity 配置已被修改。")
    result=export_config(baseline,cfg,endpoint,previous_sha256=meta.get("sha256"))
    meta.update(layout=2,freshSdkCache=True,sha256=result["sha256"],url=endpoint,sharedBytes=0,privateBytes=copied+cfg.stat().st_size)
    save(owner,meta)
    save(home/"client-config.json",meta)
    os.rename(partial,home/"Client")
    return meta


def prepare(source,home,endpoint,progress=print):
    from client_media import locate
    src=Path(source).resolve()
    home=Path(home).resolve()
    if (home/"Client").exists():
        # A migrated kit can run entirely from the already imported private copy.
        use_src=src if (src/"launcher.exe").is_file() and src!=home/"Client" else home/"Client"
        return _from_installed(use_src,home,endpoint,progress)
    if (src/"launcher.exe").is_file():
        return _from_installed(src,home,endpoint,progress)
    media=locate(src,home)
    if media is None:
        raise ValueError("请选择已安装的 TapTap 客户端，或选择配套原版 ISO / ZIP 安装媒体。")
    return _install_media(media,home,endpoint,progress)

