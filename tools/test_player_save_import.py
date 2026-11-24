"""Player-chosen progress file, redacted preview, account scoping and reversible application."""
import json
from pathlib import Path
from unittest.mock import patch
import zipfile


def backup(folder):
    folder.mkdir()
    marker="unrelated-private-test-data"
    main={"status":{"v":1},"level":2,"exp":1,"world_level":0,"player_name":marker}
    data={"format":"overfield-snapshot/1","account":{"session":marker},"endpoint":marker,
          "decoded":{"main":{"name":"PlayerMainDataRsp","frames":[{"ok":True,"decoded":main}]}},
          "stats":{"other":marker}}
    (folder/"snapshot.json").write_text(json.dumps(data),encoding="utf8")
    (folder/"private-server-import.json").write_text(json.dumps({"irrelevant":marker}),encoding="utf8")
    archive=folder/"player.zip"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as target:
        for name in ("snapshot.json","private-server-import.json"):
            target.write(folder/name,"source/"+name)
    return archive


def privacy(accounts,folder):
    archive=backup(folder)
    token="ofi_"+"A"*43
    preview="ofv_"+"a"*32
    expected={"format":"of-sf-progress/1","player":[2,1,0],"heroes":[],"quests":[],"chapters":[],
              "achievements":[],"stacks":[],"outfits":[],"gear":[],"team":[0,0,0],"loadouts":[]}
    def capture(config,verb,path,fields,max_bytes):
        assert verb=="POST" and path=="/local/v1/accounts/import-preview"
        assert fields=={"import_ticket":token,"data":expected}
        assert "unrelated-private-test-data" not in json.dumps(fields)
        return 200,{"ok":True,"eligible":True,"counts":{"角色":1},"warnings":[],"preview_id":preview}
    with patch.object(accounts,"request",side_effect=capture) as requested:
        for source in (archive,folder,folder/"snapshot.json",folder/"private-server-import.json"):
            result=accounts.import_backup("import-preview",{"path":str(source),"import_ticket":token},{})
            assert result["ok"] and result["preview_id"]==preview
        bad=folder/"broken.zip";bad.write_bytes(b"broken")
        reply=accounts.import_backup("import-preview",{"path":str(bad),"import_ticket":token},{})
        assert reply["code"]=="invalid_backup"
        assert requested.call_count==4
    return archive


def window_smoke(accounts, config, archive, token):
    import os
    import subprocess
    import sys
    if os.name!="nt":
        return False
    directory=Path(__file__).resolve().parent
    checkout=directory.parent
    shared=checkout/"client_tools/sources/ImportWindow.cs"
    if not shared.is_file():
        shared=checkout/"kit/client_tools/sources/ImportWindow.cs"
    target=archive.parent/"import-window-smoke.exe"
    compiler=Path(os.environ["WINDIR"])/"Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler),"/nologo","/utf8output","/target:exe","/platform:x64","/r:System.Windows.Forms.dll",
                    "/r:System.Drawing.dll","/r:System.Web.Extensions.dll","/out:"+str(target),str(shared),
                    str(directory/"test_import_window.cs")],check=True)
    payload={"server":config,"python":sys.executable,"api":str(Path(accounts.__file__).resolve()),"ticket":token,"backup":str(archive)}
    subprocess.run([str(target)],input=json.dumps(payload,ensure_ascii=True).encode("ascii"),check=True,timeout=55)
    return True


def over_tls(accounts, private, config, archive, who, state):
    from contextlib import closing
    import sqlite3
    previous=private("login",**who)
    existing=private("import-preview",import_ticket=previous["import_ticket"],path=str(archive))
    assert existing["ok"] and not existing["eligible"]
    who={"username":"backup_target","password":"temporary-backup-target-password"}
    target=private("register",**who,nickname="导入验收")
    ticket=target["import_ticket"]
    assert target["import_expires_in"]>0
    # Use a different private account to check a preview cannot be applied across slots.
    other=private("register",username="other_import_test",password="backup-test-password-123",nickname="导入对照")
    def submit(action,token=ticket,**kwargs):
        return private(action,import_ticket=token,**kwargs)
    def level():
        with closing(sqlite3.connect((state/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
            row=db.execute("SELECT p.level FROM players p JOIN private_slots s ON s.player_id=p.player_id WHERE s.slot=?",(target["slot"],)).fetchone()
            return row[0] if row else None
    before=level()
    cancelled=submit("import-preview",path=str(archive))
    assert cancelled.get("ok") and level()==before  # Closing the preview makes no write.
    info=submit("import-preview",path=str(archive))
    assert info.get("ok") and info.get("eligible"),info
    preview=info["preview_id"]
    assert not submit("import-apply",token=other["import_ticket"],preview_id=preview)["ok"]
    applied=submit("import-apply",preview_id=preview)
    assert applied.get("ok") and level()==2,applied
    with closing(sqlite3.connect((state/"game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
        baseline=json.loads(db.execute("SELECT prior FROM private_import_delta WHERE slot=? AND kind='level'",(target["slot"],)).fetchone()[0])[0]
    assert baseline!=2
    assert not submit("import-undo",token=other["import_ticket"])["ok"]
    restored=submit("import-undo",token=private("login",**who)["import_ticket"])
    assert restored.get("ok") and level()==baseline,restored
    fresh=private("login",**who)["import_ticket"]
    if window_smoke(accounts,config,archive,fresh):
        assert level()==baseline and submit("import-undo",token=fresh)["ok"]
    # Bigger bodies are only admitted on the explicit preview route.
    for route,size in (("login",2049),("import-apply",2049),("import-undo",2049),("import-preview",2*1024*1024+1)):
        with accounts.connection(config) as http:
            http.putrequest("POST","/local/v1/accounts/"+route)
            http.putheader("Content-Type","application/json");http.putheader("Content-Length",str(size));http.endheaders()
            assert http.getresponse().status==413
