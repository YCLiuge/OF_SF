"""Verify retail minimap collection/device points, chapter gates and reconstruction from old saves."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from private_map_progress import catalog, points
from private_story_catalog import meets_limits
from private_shop import now


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    game = LocalGameService(identity,root,resources,port=0)
    game.start()
    def connect(profile):
        sock,_,pid = login(game.port,identity.issue_ticket(*profile))
        join(sock)
        return sock,pid
    def scores(sock):
        reply = call(sock,"SceneProcessList",44)
        assert reply.status == pb.StatusCode_OK
        return {entry.scene_id:entry.process for entry in reply.scene_process_list}
    def act(sock, action, **values):
        return call(sock,action,45,**values)
    try:
        primary = identity.create_profile("地图进度")
        sock,pid = connect(primary)
        with sock:
            assert scores(sock).get(100,0)==0
            assert act(sock,"Collecting",item_id=10801001).status==pb.StatusCode_BAD_REQ
            assert act(sock,"Collecting",item_id=10800001).status==pb.StatusCode_OK
            assert act(sock,"Collecting",item_id=10800001).status==pb.StatusCode_ALREADY_COLLECTED
            assert act(sock,"TreasureBoxOpen",treasure_box_index=0,box_type=0,loc_type=pb.TreasureBoxLocType_SCENE).status==pb.StatusCode_OK
            assert scores(sock)[100]==1
            assert act(sock,"AreaUnlock",area_id=101).status==pb.StatusCode_OK
            assert act(sock,"AreaClose",area_id=101).status==pb.StatusCode_OK
            assert act(sock,"AreaUnlock",area_id=101).status==pb.StatusCode_OK
            assert scores(sock)[100]==2
            assert act(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert act(sock,"Collecting",item_id=900000000).status==pb.StatusCode_BAD_REQ
            assert act(sock,"AreaClose",area_id=1001).status==pb.StatusCode_OK
            assert scores(sock).get(1,0)==0
            totals, markers, _ = catalog()
            target=sum(totals[1].values())
            required=(target+9)//10
            crystals=sorted(item for item,loc in markers.items() if loc.get(1)==3)
            assert len(crystals)==266 and len(crystals)>required
            for item in crystals[:required-1]:
                assert act(sock,"Collecting",item_id=item).status==pb.StatusCode_OK
            assert scores(sock)[1]==required-1
            gate={"map_completion_unlock":[1,10]}
            with game.lock:
                assert not meets_limits(pid,gate,10,now())
            assert act(sock,"AreaUnlock",area_id=1001).status==pb.StatusCode_OK
            assert scores(sock)[1]==required
            with game.lock:
                assert meets_limits(pid,gate,10,now())
            assert act(sock,"AreaClose",area_id=1001).status==pb.StatusCode_OK
            assert act(sock,"AreaUnlock",area_id=1001).status==pb.StatusCode_OK
            assert scores(sock)[1]==required
        other = identity.create_profile("已有地图记录")
        sock,old = connect(other)
        with sock:
            assert scores(sock).get(1,0)==0
            # Old release saved real collection and area payloads without point keys.
            with game.lock,db.conn:
                db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='map_points'",(old,))
                for item in crystals[:2]:
                    row=pb.PBCollectionRewardData(item_id=item,status=pb.Reward)
                    db.set_collection(old,item,8,row.SerializeToString())
                db.set_collection(old,crystals[2],8,pb.PBCollectionRewardData(item_id=crystals[2]).SerializeToString())
                saved=pb.AreaData(area_id=1001,area_state=pb.AreaData.Unlock,level=2)
                db.set_area(old,1,1001,saved.SerializeToString())
                db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?, 'ach', 'exp:2', '11')",(old,))
            assert scores(sock)[1]==3 and scores(sock)[2]==11 and scores(sock).get(100,0)==0
            assert act(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert act(sock,"Collecting",item_id=crystals[0]).status==pb.StatusCode_ALREADY_COLLECTED
            assert act(sock,"Collecting",item_id=crystals[3]).status==pb.StatusCode_OK
            assert scores(sock)[1]==4
        game.stop()
        game.start()
        for profile, count in ((other,4),(primary,required)):
            sock,role=connect(profile)
            with sock:
                assert scores(sock)[1]==count
                with game.lock:
                    assert points(role,1)==count
                assert scores(sock)[1]==count
    finally:
        game.stop()
    print("PASS native minimap ownership, crystal/area idempotency, chapter threshold, box exclusion, legacy import and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
