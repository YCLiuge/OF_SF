"""Personal CN v1.5 backup projection, transactional ownership and native save visibility."""
import argparse
from contextlib import closing,contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
import zipfile

from local_identity import LocalIdentity
from local_game_service import LocalGameService,pb
from private_profiles import create_profile_server
from save_import_data import project_file
from test_private_profiles import request
from test_game_realm import login,join
from utils import db


def synthetic():
    def packet(name,data):
        return {"name":name,"frames":[{"ok":True,"decoded":{"status":{"v":1},**data}}]}
    return {"format":"overfield-snapshot/1","phone":"DO-NOT-COPY","decoded":{
        "main":packet("PlayerMainDataRsp",{"player_name":"DO-NOT-COPY","account_type":9999,"player_id":4444444,
            "level":20,"exp":322,"world_level":2,"team":{"char1":101001},
            "characters":[{"character_id":101001,"level":17,"max_level":20,"star":1,"exp":21,
                           "equipment_presets":[{"weapon":986654321}]}],
            "quest_detail":{"quests":[{"quest_id":100011,"status":{"v":3},"complete_count":1,
                           "conditions":[{"condition_id":11000111,"progress":1,"status":{"v":3}}]}],
                           "chapters":[{"chapter_id":10000,"rewarded_story_ids":[1000001]}]}}),
        "pack":packet("PackNotice",{"items":[{"main_item":{"item_id":5012011,"base_item":{"item_id":5012011,"num":53}}},
            {"main_item":{"item_id":101,"base_item":{"item_id":101,"num":43584}}},
            {"main_item":{"item_id":101,"weapon":{"weapon_id":1101104,"instance_id":986654321,"level":11,"star":2}}}]}),
        "ach":packet("WorldLevelAchieveListRsp",{"achieves":[{"achieve_id":16000201,"count":22}]}),
        "chat":packet("PlayerChatNotice",{"name":"DO-NOT-COPY"})},
        "customisation":{"dye":{"outfitsData":[{"outfit_id":4011013,"schemes":[{"scheme_index":0,"is_un_lock":True,
                     "colors":[{"pos":1,"r":50,"g":60,"b":70}]}]}]}}}


@contextmanager
def world(base,resources):
    identity=LocalIdentity(base/"identity.sqlite3")
    game=LocalGameService(identity,base,resources,port=0);game.start()
    api=create_profile_server(identity,port=0,allow_registration=True)
    api.accounts.imports.game=game
    thread=Thread(target=api.serve_forever,kwargs={"poll_interval":.02},daemon=True)
    thread.start()
    try:yield api.server_port,game
    finally:
        api.shutdown();api.server_close();thread.join();game.stop()


def run(root,resources):
    folder=root/"export";folder.mkdir()
    (folder/"snapshot.json").write_text(json.dumps(synthetic()),encoding="utf8")
    (folder/"private-server-import.json").write_text("DO-NOT-COPY",encoding="utf8")
    archive=root/"export.zip"
    with zipfile.ZipFile(archive,"w") as f:
        for path in folder.iterdir():f.write(path,"slot/"+path.name)
    data=project_file(archive)
    assert all(project_file(p)==data for p in (folder,folder/"snapshot.json",folder/"private-server-import.json"))
    assert not any(secret in json.dumps(data) for secret in ("DO-NOT-COPY","4444444","986654321"))
    with zipfile.ZipFile(root/"ambiguous.zip","w") as f:
        for name in ("a","b"):f.writestr(name+"/snapshot.json","{}")
    try:project_file(root/"ambiguous.zip");raise AssertionError("multiple snapshots accepted")
    except ValueError:pass
    credentials={"username":"import_test_a","password":"private-example-pass"}
    other={"username":"import_test_b","password":"private-example-pass"}
    state=root/"realm";state.mkdir()
    def call(action,body):
        return request(port,"POST","/local/v1/accounts/"+action,body)
    with world(state,resources) as (port,game):
        a=call("register",{**credentials,"nickname":"本服角色甲"})[1]
        b=call("register",{**other,"nickname":"本服角色乙"})[1]
        auth={"import_ticket":a["import_ticket"]};wrong={"import_ticket":b["import_ticket"]}
        assert a["import_expires_in"]==600
        assert call("import-preview",{"import_ticket":a["ticket"],"data":data})[0]==401
        assert call("import-preview",{**auth,"data":{**data,"gm":999}})[0]==400
        assert call("import-preview",{**auth,"data":{**data,"stacks":[[1,True]]}})[0]==400
        assert call("import-preview",{**auth,"data":data,"slot":b["slot"]})[0]==400
        status,info=call("import-preview",{**auth,"data":data})
        assert status==200 and info["eligible"] and info["counts"]["探索员"]==1,info
        assert db.conn.execute("SELECT COUNT(*) FROM private_slots").fetchone()[0]==0
        key={"preview_id":info["preview_id"]}
        assert call("import-apply",{**wrong,**key})[0]==409
        with patch("save_import_gameplay.restore",side_effect=ValueError("rollback")):
            assert call("import-apply",{**auth,**key})[0]==400
        assert db.conn.execute("SELECT COUNT(*) FROM private_slots").fetchone()[0]==0
        assert call("import-apply",{**auth,**key})[0]==200
        assert call("import-apply",{**auth,**key})[0]==200
        assert call("import-undo",wrong)[0]==409
        pid=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(a["slot"],)).fetchone()[0]
        assert pid>=1000001 and pid!=4444444
        assert db.conn.execute("SELECT player_name,level,exp FROM players WHERE player_id=?",(pid,)).fetchone()==("本服角色甲",20,322)
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,5012011)).main_item.base_item.num==53
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,101)).main_item.base_item.num==1000000
        hero=pb.Character.FromString(db.get_characters(pid,101001)[0])
        assert hero.level==17 and db.get_players_info(pid,"team")[0]==(101001,0,0)
        equipped=hero.equipment_presets[0].weapon
        item=pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=equipped)).main_item.weapon
        assert equipped!=986654321 and item.wearer_id==101001 and item.level==11 and item.star==2
        assert pb.Quest.FromString(db.get_quest(pid,100011)).status==3
        assert pb.Chapter.FromString(db.get_chapter(pid,10000)).rewarded_story_ids==[1000001]
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,4011013)).main_item.outfit.dye_schemes[0].colors[0].green==60
        assert pb.Achieve.FromString(db.get_achieve(pid,16000201)).count>=22
        with db.conn:db.conn.execute("UPDATE private_import_tickets SET expires=0 WHERE hash=?",(hashlib.sha256(a["import_ticket"].encode()).digest(),))
        assert call("import-undo",auth)[0]==401
    with world(state,resources) as (port,game):
        new=call("login",credentials)[1];auth={"import_ticket":new["import_ticket"]}
        assert call("import-apply",{**auth,**key})[0]==200
        assert call("import-undo",auth)[0]==200 and call("import-undo",auth)[0]==200
        assert db.conn.execute("SELECT level FROM players WHERE player_id=?",(pid,)).fetchone()[0]==10
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,5012011)).main_item.base_item.num==4
        info=call("import-preview",{**auth,"data":data})[1]
        assert info["eligible"] and call("import-apply",{**auth,"preview_id":info["preview_id"]})[0]==200
        client,info,_=login(game.port,new["ticket"])
        with closing(client):
            main,_=join(client)
            assert main.level==20 and main.scene_id==9999 and main.player_name=="本服角色甲"
            assert not call("import-preview",{**auth,"data":data})[1]["eligible"]
            assert call("import-undo",auth)[0]==409
        b=call("login",other)[1]
        with closing(login(game.port,b["ticket"])[0]):pass
        assert not call("import-preview",{"import_ticket":b["import_ticket"],"data":data})[1]["eligible"]
        assert db.conn.execute("SELECT sealed FROM private_import_receipt WHERE slot=?",(a["slot"],)).fetchone()[0]==1
    print("PASS: v1.5 ZIP/folder/file projection, account isolation, read-only preview, atomic rollback, retry, expiry, restart, undo and native progress.")


def check_gateway(root,resources):
    from http.client import HTTPConnection
    import time
    from player_tls_channels import PlayerTlsChannels
    from test_native_player_gateway import certificates,gateway,ClearChannels
    base=root/"gateway-world";base.mkdir()
    cert=certificates(root)
    with world(base,resources) as (port,game),gateway(port,game.port,cert,[],base) as gw:
        channels=(PlayerTlsChannels(f"https://localhost:{gw}",str(cert/"ca.pem"),account_port=port,game_port=game.port)
                  if cert else ClearChannels(gw,port,game.port))
        def post(route,fields,*,declared=None):
            with channels.open(port) as sock:
                conn=HTTPConnection("localhost",gw,timeout=8);conn.sock=sock
                raw=json.dumps(fields).encode()
                path="/local/v1/accounts/"+route
                if declared is not None:
                    conn.putrequest("POST",path)
                    conn.putheader("Content-Length",str(declared))
                    conn.putheader("Content-Type","application/json")
                    conn.endheaders()
                else:conn.request("POST",path,raw,{"Content-Type":"application/json"})
                res=conn.getresponse();buf=res.read()
                return res.status,json.loads(buf) if buf else {}
        from save_import_data import project
        data=project(synthetic())
        assert post("import-preview",{},declared=2*1024*1024+1)[0]==413
        assert all(post(a,{},declared=2049)[0]==413 for a in ("import-apply","import-undo","login"))
        accounts=[post("register",{"username":"gateway_import_"+s,"password":"example-private-pass",
                                   "nickname":"import"+s})[1] for s in ("a","b")]
        a,b=({"import_ticket":v["import_ticket"]} for v in accounts)
        assert post("import-preview",{"import_ticket":accounts[1]["ticket"],"data":data})[0]==401
        # A JSON body above the ordinary account limit is accepted only on the preview route.
        data["stacks"].extend([[5012011,53]]*250)
        assert len(json.dumps(data).encode())>2048
        status,first=post("import-preview",{**a,"data":data})
        assert status==200 and first["eligible"]
        key={"preview_id":first["preview_id"]}
        assert post("import-apply",{**b,**key})[0]==409
        assert post("import-apply",{**a,**key})[0]==200
        _,second=post("import-preview",{**b,"data":data})
        with game.lock,db.conn:
            db.conn.execute("UPDATE private_import_preview SET expires=0 WHERE id=?",(second["preview_id"],))
            db.conn.execute("UPDATE private_import_tickets SET expires=0 WHERE slot=?",(accounts[1]["slot"],))
            prior=db.conn.execute("SELECT COUNT(*) FROM private_import_delta WHERE slot=?",(accounts[0]["slot"],)).fetchone()[0]
            assert prior>0
            game.next_import_cleanup=0
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with game.lock:
                pending=db.conn.execute("SELECT COUNT(*) FROM private_import_preview WHERE id=?",(second["preview_id"],)).fetchone()[0]
            if not pending:break
            time.sleep(.05)
        assert not pending
        with game.lock:
            assert db.conn.execute("SELECT COUNT(*) FROM private_import_tickets WHERE slot=?",(accounts[1]["slot"],)).fetchone()[0]==0
            assert db.conn.execute("SELECT COUNT(*) FROM private_import_delta WHERE slot=?",(accounts[0]["slot"],)).fetchone()[0]==prior
            assert db.conn.execute("SELECT COUNT(*) FROM authority.realm_accounts").fetchone()[0]==2
        assert post("import-apply",{**b,"preview_id":second["preview_id"]})[0]==401
        assert post("import-apply",{**a,**key})[0]==200 and post("import-undo",a)[0]==200
        with closing(login(game.port,accounts[1]["ticket"])[0]):pass
    print("PASS: verified TLS, per-route limits, scoped account tickets and expiry cleanup." if cert else
          "PASS: gateway limits, ticket scope and cleanup; TLS certificate generation unavailable.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.resources)
        check_gateway(Path(tmp),args.resources)
