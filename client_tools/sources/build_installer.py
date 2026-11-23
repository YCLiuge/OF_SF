"""Package the Windows native-client kit. The game source and private player state are external."""
from pathlib import Path
import argparse, hashlib, json, os, stat, subprocess, tempfile, zipfile

TOP={'OverField-Server','client_tools','开放空间私服.exe','区服设置.cmd','of-launch.cmd','玩家说明.txt'}
IGNORE={'state','client','profiles','__pycache__','.git','.pytest_cache','logs','client-config.json','runtime-linux','bin','obj','.vs'}
SUFFIX={'.pyc','.pyo','.sqlite','.sqlite3','.db','.pcap','.etl','.dmp','.log'}
def main(kit,dest):
    kit=kit.resolve();dest=dest.resolve()
    if {name for name in TOP if not (kit/name).exists()}:
        raise ValueError('安装载荷未齐备。')
    src=kit/'client_tools/sources'
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='overfield-setup-build-',dir=dest.parent) as tmp:
        tmp=Path(tmp);payload=tmp/'payload.zip';index=tmp/'manifest.json'
        manifest={}
        with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name in sorted(TOP):
                top=kit/name
                files=sorted(top.rglob('*')) if top.is_dir() else [top]
                for file in files:
                    rel=file.relative_to(kit)
                    if any(part.lower() in IGNORE for part in rel.parts) or file.suffix.lower() in SUFFIX:continue
                    if file.is_symlink() or file.is_junction():raise ValueError('Symbolic link in release: '+str(file))
                    if not file.is_file():continue
                    data=file.read_bytes();key=rel.as_posix()
                    manifest[key]=hashlib.sha256(data).hexdigest()
                    item=zipfile.ZipInfo(key,(2026,11,24,0,0,0))
                    item.create_system=3;item.external_attr=(stat.S_IFREG|0o644)<<16
                    archive.writestr(item,data,zipfile.ZIP_DEFLATED,compresslevel=6)
        index.write_text(json.dumps({'product':'OverField.PrivateNativeKit','files':manifest},separators=(',',':'),ensure_ascii=False),encoding='utf8')
        csc=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        artifact=tmp/'private-install.exe'
        subprocess.run([str(csc),'/nologo','/target:winexe','/platform:anycpu','/optimize+','/codepage:65001','/utf8output',
                        '/out:'+str(artifact),'/win32manifest:'+str(src/'private-launcher.manifest'),
                        '/resource:'+str(index)+',OverField.Manifest','/resource:'+str(payload)+',OverField.Payload',
                        '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',
                        '/reference:System.IO.Compression.dll',str(src/'NativeInstaller.cs')],check=True)
        artifact.replace(dest)
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_name(dest.name+'.sha256').write_text(digest+'  '+dest.name+'\n',encoding='utf8')
    print(f'{len(manifest)} files; {dest.stat().st_size} bytes; sha256 {digest}',flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('kit',type=Path);parser.add_argument('output',type=Path)
    a=parser.parse_args();main(a.kit,a.output)

