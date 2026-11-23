"""Run against a private loopback realm and two newly provisioned synthetic players."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg


def call(c, name, **kwargs):
    req = getattr(pb, name+"Req")(**kwargs)
    c.sendall(encode(getattr(MsgId,name+"Req"), req.SerializeToString(), 27))
    return next_msg(c, getattr(MsgId,name+"Rsp"), getattr(pb,name+"Rsp"))


def loot(items):
    return {it.main_item.item_id: it.main_item.base_item.num for it in items}


def run(root, resources):
    seeds=json.loads((resources/"data/ScenesConfigAsset.json").read_bytes())["Dungeons"]
    scene=next(d for d in seeds if d["ID"]==9600104)
    boxes=json.loads((resources/"data/TreasureBox.json").read_bytes())["treasure_box"]["datas"]
    normal={d["i_d"] for d in boxes if d.get("new_treasure_box_reward_type")==1}
    identity=LocalIdentity(root/"identity.sqlite3")
    a=identity.create_profile("探险甲")
    b=identity.create_profile("探险乙")
    host=LocalGameService(identity,root,resources,port=0)
    def connect(profile):
        c,_,_=login(host.port,identity.issue_ticket(*profile))
        _,world=join(c)
        return c,world
    def inventory(pid):
        with closing(sqlite3.connect(root/"game.sqlite")) as conn:
            return {item:pb.ItemDetail.FromString(raw).main_item.base_item.num for item,raw in
                    conn.execute("SELECT item_id,item_detail_blob FROM items WHERE player_id=?",(pid,))}

    def move(c, x, z):
        req=pb.PlayerSceneRecordReq()
        item=req.data.char_recorder_data_lst.add(char_id=101001)
        item.pos.x, item.pos.z, item.rot.y = x,z,4500
        c.sendall(encode(MsgId.PlayerSceneRecordReq,req.SerializeToString(),35))
        notice=next_msg(c,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
        assert notice.data[0].data[0].char_recorder_data_lst[0].pos.x==x

    did=9600104
    host.start()
    try:
        first,home=connect(a)
        pid=home.data.players[0].player_id
        other,_=connect(b)
        with first, other:
            move(first,100,200)
            # Drain the home movement sent to the peer before the instance begins.
            next_msg(other, MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            # Unclaimed objectives, invalid characters, invalid IDs and no live run.
            assert call(first,"DungeonTaskFinishReward",dungeon_id=did).status==pb.StatusCode_DungeonCantReward
            assert call(first,"DungeonEnter",dungeon_id=11101,char1=999).status==pb.StatusCode_BAD_REQ
            assert call(first,"DungeonFinish").status==pb.StatusCode_PlayerNotInDungeon
            assert call(first,"DungeonView",dungeon_id=0).status==pb.StatusCode_CFG_NOT_FOUND
            entry=call(first,"DungeonEnter",dungeon_id=11101,char1=101001)
            assert entry.status==pb.StatusCode_OK and entry.dungeon_data.enter_times==1
            assert (entry.team.char1.pos.x,entry.team.char1.pos.z)==(-5047,3721)
            move(first,-4010,3300)
            repeated=call(first,"DungeonEnter",dungeon_id=11101,char1=101001)
            assert repeated.dungeon_data.enter_times==1 and repeated.team.char1.pos.x==-4010
            assert call(first,"ChangeSceneChannel",scene_id=9999,channel_label=1).status==pb.StatusCode_BAD_REQ
            assert call(other,"ChangeSceneChannel",scene_id=9999,channel_label=1).status==pb.StatusCode_OK
            peer_snapshot=next_msg(other,MsgId.SceneDataNotice,pb.SceneDataNotice)
            assert pid not in [p.player_id for p in peer_snapshot.data.players]
            assert call(first,"CollectCoin",coin_index=40000).status==pb.StatusCode_BAD_REQ
            assert call(first,"CollectCoin",coin_index=0).status==pb.StatusCode_BAD_REQ
            move(first,-1025,4034)
            assert loot(call(first,"CollectCoin",coin_index=0).rewards)=={106:1}
            assert not call(first,"CollectCoin",coin_index=0).rewards
            for index in [0,1,2,3,4,1]:
                call(first,"MonsterDead",monster_index=index,monster_id=1014)
            v=call(first,"DungeonView",dungeon_id=11101)
            assert {r.achieve_id:r.count for r in v.task_data_lst}=={20101013:4,20101014:1}
            assert call(first,"DungeonFinish").dungeon_data.finish_times==1
            assert call(first,"DungeonFinish").dungeon_data.finish_times==1
            assert call(first,"DungeonExit").status==pb.StatusCode_OK
            back=next_msg(first,MsgId.SceneDataNotice,pb.SceneDataNotice)
            returned=next(p for p in back.data.players if p.player_id==pid).team.char1.pos
            assert (returned.x,returned.z)==(100,200)
            assert call(first,"DungeonExit").status==pb.StatusCode_OK
            entry=call(first,"DungeonEnter",char1=101001,dungeon_id=did)
            assert entry.status==pb.StatusCode_OK and len(entry.treasure_boxes)==len(scene["CollectionTreasureInfos"])
            for point in scene["MonsterInfos"]:
                idx=point.get("Index",0)
                call(first,"MonsterDead",monster_index=idx,monster_id=point["UnitID"])
                call(first,"MonsterDead",monster_index=idx,monster_id=point["UnitID"])
            for point in scene["CollectionTreasureInfos"]:
                if point["ID"] in normal:
                    r=call(first,"TreasureBoxOpen",treasure_box_index=point.get("Index",0),loc_type=pb.TreasureBoxLocType_DUNGEON)
                    assert r.status==pb.StatusCode_OK
            for point in scene["CoinInfos"]:
                r=call(first,"CollectCoin",coin_index=point.get("Index",0))
                assert loot(r.rewards)=={106:1}
            v=call(first,"DungeonView",dungeon_id=did)
            assert v.dungeon_data.all_task_finished and sorted(a.count for a in v.task_data_lst)==[3,6,7]
            assert call(first,"DungeonFinish").dungeon_data.finish_times==1
            before=inventory(pid)
            claim=call(first,"DungeonTaskFinishReward",dungeon_id=did)
            assert claim.status==pb.StatusCode_OK
            assert loot(claim.rewards)=={102:50,101:5000,6016:2,6015:10,6017:6}
            assert call(first,"DungeonTaskFinishReward",dungeon_id=did).status==pb.StatusCode_REPEAT_GET_REWARD
            after=inventory(pid)
            assert {k:after[k]-before.get(k,0) for k in loot(claim.rewards)}==loot(claim.rewards)
            assert call(other,"DungeonTaskFinishReward",dungeon_id=did).status==pb.StatusCode_DungeonCantReward
            assert not call(other,"DungeonView",dungeon_id=did).task_data_lst
            assert call(first,"DungeonExit").status==pb.StatusCode_OK
    finally:
        host.stop()
    host.start()
    try:
        first, world=connect(a)
        with first:
            assert inventory(pid)==after
            restored=next(p for p in world.data.players if p.player_id==pid).team.char1.pos
            assert (restored.x,restored.z)==(100,200)
            assert {d.dungeon_id:d.finish_times for d in world.data.dungeons}=={11101:1,did:1}
            v=call(first,"DungeonView",dungeon_id=did)
            assert (v.dungeon_data.enter_times,v.dungeon_data.exit_times,v.dungeon_data.task_finish_reward)==(1,1,pb.Reward)
            assert list(v.dungeon_data.coins)==[r.get("Index",0) for r in scene["CoinInfos"]]
            assert call(first,"DungeonTaskFinishReward",dungeon_id=did).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        host.stop()
    print("PASS real private TCP: 11101 spawn/coin/enemies/finish, 9600104 three objectives and correct reward, duplicate events, player isolation, restart")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)

