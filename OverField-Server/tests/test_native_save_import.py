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
from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode,receive
from private_profiles import create_profile_server
from save_import_data import project_file
from test_private_profiles import request
from test_game_realm import login
from test_observed_initialization import call as native_call
from utils import db


def synthetic():
    def packet(name,data):
        return {"name":name,"frames":[{"ok":True,"decoded":{"status":{"v":1},**data}}]}
    return {"format":"overfield-snapshot/1","phone":"DO-NOT-COPY","decoded":{
        "main":packet("PlayerMainDataRsp",{"player_name":"旧服角色甲","account_type":9999,"player_id":3129754,"player_label":4444444,
            "level":20,"exp":322,"world_level":2,"team":{"char1":101001},
            "characters":[{"character_id":101001,"level":17,"max_level":20,"star":1,"exp":21,
                           "equipment_presets":[{"weapon":986654321}],"in_use_outfit_preset_index":2,
                           "outfit_presets":[{"preset_index":2,"pend_top":4200744,"pend_up_face":4011044,
                                    "pend_down_face":4200045,"clothes":4011013,"clothes_dye_scheme_index":1,
                                    "outfit_hide_info":{"hide_braid":True,"hide_orn":True}},
                                   {"preset_index":0,"hair":4011012,"hair_dye_scheme_index":23,
                                    "pend_top":4011013}]}],
            "quest_detail":{"quests":[{"quest_id":100011,"status":{"v":3},"complete_count":1,
                           "conditions":[{"condition_id":11000111,"progress":1,"status":{"v":3}}]}],
                           "chapters":[{"chapter_id":10000,"rewarded_story_ids":[1000001]}]}}),
        "pack":packet("PackNotice",{"items":[{"main_item":{"item_id":5012011,"base_item":{"item_id":5012011,"num":53}}},
            {"main_item":{"item_id":101,"base_item":{"item_id":101,"num":43584}}},
            {"main_item":{"item_id":101,"weapon":{"weapon_id":1101104,"instance_id":986654321,"level":11,"star":2}}}]}),
        "ach":packet("WorldLevelAchieveListRsp",{"achieves":[{"achieve_id":16000201,"count":22}]}),
        "chat":packet("PlayerChatNotice",{"name":"DO-NOT-COPY"})},
        "customisation":{"dye":{"outfitsData":[{"outfit_id":4011013,"schemes":[{"scheme_index":0,"is_un_lock":True,
                     "colors":[{"pos":1,"r":50,"g":60,"b":70}]},{"scheme_index":1,"is_un_lock":True,
                     "colors":[{"pos":1,"r":20,"g":40,"b":80}]}]}]}}}


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


def read_inherited_role(client,catalog):
    """Check the inventory and unlock lists actually delivered to the original client."""
    client.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
    stocked=set()
    actions=set()
    main=scene=None
    for _ in range(300):
        header,raw=receive(client)
        if header.msg_id==MsgId.PlayerMainDataRsp:
            main=pb.PlayerMainDataRsp.FromString(raw)
        elif header.msg_id==MsgId.SceneDataNotice:
            scene=pb.SceneDataNotice.FromString(raw)
        elif header.msg_id==MsgId.PackNotice:
            for detail in pb.PackNotice.FromString(raw).items:
                item=detail.main_item
                if (item.HasField("outfit") and item.outfit.outfit_id==item.item_id
                        or item.HasField("base_item") and item.base_item.num>0):
                    stocked.add(item.item_id)
        elif header.msg_id==MsgId.AchieveActionUnLockNotice:
            actions.update(pb.AchieveActionUnLockNotice.FromString(raw).action_id_lst)
        elif header.msg_id==MsgId.ChatUnLockExpressionNotice:
            assert set(pb.ChatUnLockExpressionNotice.FromString(raw).expression_id)==catalog.emoji
            break
    else:
        raise AssertionError("Missing complete native collection notices")
    assert main is not None and scene is not None and main.scene_id==scene.data.scene_id
    assert actions==catalog.menu_actions
    assert stocked.issuperset(catalog.items) and stocked.issuperset(catalog.head_stickers)
    heads=native_call(client,"UnlockHeadList")
    assert heads.status==pb.StatusCode_OK and set(heads.heads)==set(catalog.head_stickers)
    return main,scene


def run(root,resources):
    folder=root/"export";folder.mkdir()
    fixture=synthetic()
    from save_import_data import project
    assert "pets" not in project(fixture)
    def record(name,data):
        return {"name":name,"frames":[{"ok":True,"decoded":{"status":{"v":1},**data}}]}
    fixture["decoded"]["pet_capacity"]=record("PetWarehouseLimitNotice",{"warehouse_limit":100})
    assert "pets" not in project(fixture)
    pet={"item_id":200001009,"instance_id":986654322,"name":"雪团","level":1,"nature_id":132,
         "integrity_id":100030,"integrity":[{"attr":{"v":5},"value":14}],"special_color":True,
         "catch_scene_id":1,"catch_area_id":1001,"catch_time":1770000000,"size":243,"weight":162840}
    fixture["decoded"]["main"]["frames"][0]["decoded"]["pet_instance_id"]=pet["instance_id"]
    fixture["decoded"]["pack"]["frames"][0]["decoded"]["items"].append({"main_item":{"item_id":pet["item_id"],"pet":pet}})
    fixture["decoded"]["pet_page"]=record("GetPetRsp",{"total_num":3,"pets":[pet]})
    fixture["decoded"]["pet_capacity"]=record("PetWarehouseLimitNotice",{"warehouse_limit":125})
    (folder/"snapshot.json").write_text(json.dumps(fixture),encoding="utf8")
    (folder/"private-server-import.json").write_text("DO-NOT-COPY",encoding="utf8")
    archive=root/"export.zip"
    with zipfile.ZipFile(archive,"w") as f:
        for path in folder.iterdir():f.write(path,"slot/"+path.name)
    data=project_file(archive)
    assert all(project_file(p)==data for p in (folder,folder/"snapshot.json",folder/"private-server-import.json"))
    assert data["identity"] == {"uid":4444444,"nickname":"旧服角色甲"}
    assert not any(secret in json.dumps(data) for secret in ("DO-NOT-COPY","986654321","986654322"))
    with zipfile.ZipFile(root/"ambiguous.zip","w") as f:
        for name in ("a","b"):f.writestr(name+"/snapshot.json","{}")
    try:project_file(root/"ambiguous.zip");raise AssertionError("multiple snapshots accepted")
    except ValueError:pass
    data["identity"]["public_label"] = data["identity"].pop("uid")
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
        older=deepcopy(data)
        older["heroes"][0][6]=[[0,4011012,4011013,0,0,0,0,0]]
        assert call("import-preview",{**auth,"data":older})[0]==200
        duplicate=deepcopy(data)
        duplicate["heroes"][0][6][1][0]=2
        assert call("import-preview",{**auth,"data":duplicate})[0]==400
        assert call("import-preview",{**auth,"data":{**data,"stacks":[[1,True]]}})[0]==400
        assert call("import-preview",{**auth,"data":data,"slot":b["slot"]})[0]==400
        status,info=call("import-preview",{**auth,"data":data})
        assert status==200 and info["eligible"] and info["counts"]["探索员"]==1,info
        assert info["counts"]["可恢复宠物"]==1 and info["counts"]["缺少可用明细的宠物"]==2
        assert any("共 3 只" in msg and "明细 1 只" in msg for msg in info["warnings"])
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
        from private_pets import capacity,owned_pets
        equipped_pet,=db.get_players_info(pid,"pet_instance_id")
        companion,=owned_pets(pid)
        assert equipped_pet==companion.instance_id and equipped_pet!=986654322 and capacity(pid)==125
        assert companion.name=="雪团" and companion.integrity[0].value==14 and companion.special_color
        assert (companion.catch_scene_id,companion.catch_time,companion.size,companion.weight)==(1,1770000000,243,162840)
        assert pid>=1000001 and pid!=4444444
        assert db.conn.execute("SELECT player_name,level,exp FROM players WHERE player_id=?",(pid,)).fetchone()==("旧服角色甲",20,322)
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,5012011)).main_item.base_item.num==53
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,101)).main_item.base_item.num==1000000
        hero=pb.Character.FromString(db.get_characters(pid,101001)[0])
        selected=hero.outfit_presets[hero.in_use_outfit_preset_index]
        assert (selected.preset_index,selected.pend_top,selected.pend_up_face)==(2,4200744,4011044),selected
        assert selected.pend_down_face==4200045 and selected.clothes_dye_scheme_index==1
        assert selected.outfit_hide_info.hide_orn and selected.outfit_hide_info.hide_braid
        assert hero.outfit_presets[0].hair==4011012 and hero.outfit_presets[0].hair_dye_scheme_index==0
        assert hero.outfit_presets[0].pend_top==0
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
        assert not list(owned_pets(pid)) and not db.get_players_info(pid,"pet_instance_id")[0] and capacity(pid)==100
        assert db.conn.execute("SELECT level FROM players WHERE player_id=?",(pid,)).fetchone()[0]==10
        assert pb.ItemDetail.FromString(db.get_item_detail(pid,5012011)).main_item.base_item.num==4
        info=call("import-preview",{**auth,"data":data})[1]
        assert info["eligible"] and call("import-apply",{**auth,"preview_id":info["preview_id"]})[0]==200
        client,info,_=login(game.port,new["ticket"])
        with closing(client):
            main,scene=read_inherited_role(client,game.entitlements)
            assert main.pet_instance_id and main.pet_instance_id==next(owned_pets(pid)).instance_id
            visible=next(player for player in scene.data.players if player.player_id==main.player_id)
            assert (visible.pet_item_id,visible.pet_name)==(200001009,"雪团")
            native_hero=next(h for h in main.characters if h.character_id==101001)
            native_look=native_hero.outfit_presets[native_hero.in_use_outfit_preset_index]
            assert native_look.clothes_dye_scheme_index==1 and native_look.pend_top==4200744 and native_look.outfit_hide_info.hide_braid
            assert main.level==20 and main.scene_id==9999 and main.player_name=="旧服角色甲" and main.player_id==pid and main.player_label==4444444
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
        accounts=[]
        for suffix in ("a","b"):
            fields={"username":"gateway_import_"+suffix,"password":"example-private-pass","nickname":"import"+suffix}
            assert post("register",fields)==(428,{"error":"captcha_required"})
            status,receipt=request(port,"POST","/local/v1/accounts/register",fields)
            assert status==201
            accounts.append(receipt)
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
