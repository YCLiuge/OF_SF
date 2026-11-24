"""Opening-scene checkpoints and resuming the backed-up actor through native login."""
import argparse
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import pb
from private_bottle_intro import is_enrolled
from save_import_data import BackupFormatError,project
from save_import_gameplay import validate
from save_import_location import destination
from test_native_save_import import synthetic,world
from test_private_profiles import request
from test_game_realm import join,login
from utils import db


def packet(data):
    return {"ok":True,"decoded":{"status":{"v":1},"data":data}}


def participant(player,x):
    return {"player_id":player,"team":{"char1":{"pos":{"x":x,"y":-82,"z":65},"rot":{"y":-400}}}}


def archive(scene,progress):
    source=synthetic()
    main=source["decoded"]["main"]["frames"][0]["decoded"]
    # A portable archive without a nickname may still resume gameplay on a locally named account.
    main.pop("player_name",None)
    own=main["player_id"]
    if not progress:main["quest_detail"]={"chapters":[],"quests":[]}
    main.update(scene_id=scene,channel_id=41)
    source["decoded"]["scene"]={"name":"SceneDataNotice","frames":[
        packet({"scene_id":scene,"channel_id":42,"players":[participant(own,98)]}),
        packet({"scene_id":scene,"channel_id":41,"players":[participant(123,88),participant(own,17)],
                "areas":[{"area_id":101,"area_state":{"v":1}}]}),
        packet({"scene_id":scene,"channel_id":41,"players":[participant(own,77)]})]}
    return source


def verify(port,game,suffix,data,scene,device):
    account={"username":"progress_"+suffix,"password":"isolated-private-testing","nickname":"测试"+suffix}
    def call(action,body):
        status,out=request(port,"POST","/local/v1/accounts/"+action,body)
        assert status in (200,201), (action,status)
        return out
    auth=call("register",account)
    token={"import_ticket":auth["import_ticket"]}
    preview=call("import-preview",{**token,"data":data})
    assert preview["eligible"] and any("进度接续" in s for s in preview["warnings"])
    applied={"preview_id":preview["preview_id"],**token}
    call("import-apply",applied)
    pid=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(auth["slot"],)).fetchone()[0]
    assert db.conn.execute("SELECT count(*) FROM private_poses WHERE player_id=?",(pid,)).fetchone()[0]==1
    if device:assert db.get_area(pid,100,101)
    call("import-undo",token)
    assert not db.conn.execute("SELECT 1 FROM private_poses WHERE player_id=?",(pid,)).fetchone()
    assert not db.get_area(pid,100,101) and is_enrolled(pid)=="active"
    preview=call("import-preview",{**token,"data":data})
    call("import-apply",{**token,"preview_id":preview["preview_id"]})
    client,opening,_=login(game.port,auth["ticket"])
    with closing(client):
        main,notice=join(client)
        assert opening.scene_id==main.scene_id==notice.data.scene_id==scene
        assert main.channel_id==game.realm_rules.public_min
        actor=next(p for p in notice.data.players if p.player_id==main.player_id)
        expected=data["progress_location"]["pose"]
        for field in ("pos","rot"):
            value=getattr(actor.team.char1,field)
            assert [value.x,value.y,value.z]==expected[field]
        assert is_enrolled(pid)==("active" if scene==100 else "departed")
        if device:assert any(a.area_id==101 and a.area_state==pb.AreaData.Unlock for a in notice.data.areas)
        else:assert not db.get_area(pid,100,101)
        if not data["quests"]:assert not main.quest_detail.quests


def run(root,resources):
    with world(root,resources) as (port,game):
        field=archive(9999,True)
        frames=field["decoded"]["scene"]["frames"]
        frames[0]["decoded"]["data"]["gather_limits"]=[{"gather_type":3,"gather_num":880}]
        frames[1]["decoded"]["data"]["gather_limits"]=[{"gather_type":3,"gather_num":111,"gather_limit_num":6},
                                                       {"gather_num":118,"lucky_gather_limit_num":3}]
        frames[2]["decoded"]["data"]["gather_limits"]=[{"gather_type":3,"gather_num":100}]
        data=project(field)
        assert data["gather_totals"]==[[9999,0,118],[9999,3,111]]
        data.pop("identity",None)
        assert validate(data)[0]["gather_totals"]==data["gather_totals"]
        assert not validate({**data,"gather_totals":[[1,3,888]]})[0]["gather_totals"]
        auth=request(port,"POST","/local/v1/accounts/register",{
            "username":"gather.archive","password":"private-import-validation","nickname":"采集旧档"})[1]
        key={"import_ticket":auth["import_ticket"]}
        def restored(action,body):
            code,out=request(port,"POST","/local/v1/accounts/import-"+action,body)
            assert code==200
            return out
        for undo in (True,False):
            preview=restored("preview",{**key,"data":data})
            assert preview["counts"]["场景采集记录"]==2
            restored("apply",{**key,"preview_id":preview["preview_id"]})
            if undo:
                restored("undo",key)
                assert not db.conn.execute("SELECT 1 FROM field_gather_counts").fetchone()
        sock,_,_=login(game.port,auth["ticket"])
        with closing(sock):
            _,scene=join(sock)
            counts={c.gather_type:(c.gather_num,c.gather_limit_num,c.lucky_gather_limit_num)
                    for c in scene.data.gather_limits}
            assert counts=={0:(118,0,0),3:(111,0,0)}
        for scene,suffix,progress in ((100,"bottle",False),(1,"sakura",True)):
            data=project(archive(scene,progress))
            data.pop("identity",None)
            assert data["progress_location"]["pose"]=={"pos":[17,-82,65],"rot":[0,-400,0]}
            verify(port,game,suffix,data,scene,device=scene==100)
        missing=archive(100,False)
        duplicate=archive(100,False)
        frames=duplicate["decoded"]["main"]["frames"]
        frames.append({"ok":True,"decoded":{**frames[0]["decoded"],"player_id":123456}})
        try:project(duplicate)
        except BackupFormatError:pass
        else:raise AssertionError("multi-owner export accepted")
        main=missing["decoded"]["main"]["frames"][0]["decoded"]
        main["scene_id"]=9999
        assert project(missing)["progress_location"]=={"scene":9999}
        main["scene_id"]=100
        main["player_id"]+=8
        assert project(missing)["progress_location"]=={"scene":100}
        main.pop("scene_id")
        early=project(missing)
        early.pop("identity",None)
        assert "progress_location" not in early and destination(validate(early)[0])==100
        old=project(synthetic())
        old.pop("identity",None)
        assert destination(validate(old)[0])==9999
        # At the windmill the first quest exists but its NPC objective has not begun in the plaza.
        old["chapters"]=[]
        old["quests"][0][1:]=[1,0,[[11000111,1,0]]]
        assert destination(validate(old)[0])==100
        unknown={**old,"progress_location":{"scene":999998,"pose":{"pos":[1,2,3],"rot":[0,0,0]}}}
        assert "progress_location" not in validate(unknown)[0]
        for invalid in ({"scene":True},{"scene":100,"pose":{"pos":[True,0,0],"rot":[0,0,0]}},
                        {"scene":9999,"bottle_device_open":True},{"scene":100,"channel_id":9}):
            try:validate({**old,"progress_location":invalid})
            except ValueError:pass
            else:raise AssertionError("invalid source location accepted")
    print("PASS: opening and windmill checkpoints, native scene/pose, starter and advanced story, neutral realm channel and undo.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    args=parser.parse_args()
    with TemporaryDirectory() as folder:run(Path(folder),args.resources)
