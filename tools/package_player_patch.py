"""Build the portable Windows player patch from a hydrated OF_SF source checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from hydrate import REPO, SERVER, TEMPLATE, TEMPLATE_SHA256, SERVER_TEMPLATE, input_records, local_path

TITLE="OF_SF 玩家补丁.exe"
OUTPUT="OF_SF-PlayerPatch-1.1.12-Windows-x64.zip"
MARKER=".of-player-patch.json"
INDEX="player-files.json"
CLIENT=("native_endpoint.py","client_media.py","native_launch.py","prepare_client.py","realm_addresses.py",
        "private_account_api.py","private_channel_privacy.py","player_patch.py")


def build(repo, output, *, compiler=None):
    repo=repo.resolve()
    output=output.resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    compiler=compiler or Path(os.environ["WINDIR"])/"Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    records=input_records(repo)
    with tempfile.TemporaryDirectory(prefix=".of-player-",dir=output.parent) as work:
        temp=Path(work)
        exe=temp/TITLE
        subprocess.run([str(compiler),"/nologo","/utf8output","/target:winexe","/platform:x64","/optimize+",
                        "/out:"+str(exe),"/r:System.Windows.Forms.dll","/r:System.Drawing.dll",
                        "/r:System.Web.Extensions.dll",str(repo/"client_tools/sources/PlayerPatch.cs"),
                        str(repo/"client_tools/sources/ImportWindow.cs")],check=True)
        entries={TITLE:(exe,None),"玩家说明.md":(repo/"client_tools/PLAYER_PATCH.md",None)}
        entries.update({"client_tools/"+n:(repo/"client_tools"/n,None) for n in CLIENT})
        entries[TEMPLATE]=(repo/TEMPLATE,TEMPLATE_SHA256)
        server=repo/SERVER
        for key in ("of.py","LICENSE-of-ps.txt"):
            entries[SERVER+"/"+key]=(server/key,None)
        for path in (server/"app").rglob("*.py"):
            if "__pycache__" not in path.parts:
                entries[path.relative_to(repo).as_posix()]=(path,None)
        for name, record in records.items():
            if name.startswith(("resources/","runtime-win64/","vendor/common/","vendor/win64/")) or name==SERVER_TEMPLATE:
                entries[SERVER+"/"+name]=(local_path(server,name),record["sha256"])
        target=temp/"player.zip"
        per_file={}
        with zipfile.ZipFile(target,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            def add(key, data):
                info=zipfile.ZipInfo(key)
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
            for key,(path,expected) in sorted(entries.items()):
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Expected plain input: "+key)
                data=path.read_bytes()
                digest=hashlib.sha256(data).hexdigest()
                if expected is not None and digest!=expected:
                    raise ValueError("Unexpected player build input: "+key)
                add(key,data)
                per_file[key]=digest
            settings_command = '@echo off\r\nchcp 65001 >nul\r\nstart "" "%~dp0OF_SF 玩家补丁.exe" --realm-picker\r\n'.encode("utf8")
            add("区服设置.cmd", settings_command)
            per_file["区服设置.cmd"] = hashlib.sha256(settings_command).hexdigest()
            add(MARKER,b'{"product":"OF_SF-Player-1.1.12"}\n')
            add(INDEX,json.dumps({"version":1,"files":per_file},ensure_ascii=False,sort_keys=True).encode())
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        side=temp/"sha256"
        side.write_text(digest+"  "+output.name+"\n",encoding="utf8")
        os.replace(target,output)
        os.replace(side,output.with_name(output.name+".sha256"))
    return output


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=REPO)
    parser.add_argument("--output",type=Path,default=REPO/"release"/OUTPUT)
    options=parser.parse_args()
    print(build(options.repo,options.output))
