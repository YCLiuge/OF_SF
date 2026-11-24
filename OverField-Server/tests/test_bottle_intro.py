"""Create a private account through the normal public API and play native SceneGuide transactions."""
import argparse
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from test_private_profiles import request, serve


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    game = LocalGameService(identity, root, resources, port=0)
    game.start()

    def db_value(pid, item):
        with closing(sqlite3.connect(root / "game.sqlite")) as conn:
            row = conn.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?", (pid,item)).fetchone()
        return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0

    def phase(sock, key, name=""):
        return call(sock,"Tutorial",50+key,tutorial_id=key,tutorial_name=name).status

    def open_chest(sock, index, kind=pb.TREASURE_BOX_TYPE_STATIC):
        reply = call(sock,"TreasureBoxOpen",60,treasure_box_index=index,box_type=kind,loc_type=pb.TreasureBoxLocType_SCENE)
        assert reply.status == pb.StatusCode_OK and reply.items
        pickup = call(sock,"TreasureBoxPickup",61,box_index=index,pick_index=-1,box_type=kind,loc_type=pb.TreasureBoxLocType_SCENE)
        assert pickup.status == pb.StatusCode_OK and pickup.items

    try:
        with serve(identity) as http:
            first = {"username":"bottle.intro","password":"test-only-intro-secret"}
            second = dict(first,username="other.intro",nickname="独立队长")
            post = lambda op, body: request(http, "POST", "/local/v1/accounts/"+op, body)
            status, registered = post("register", first)
            assert status == 201 and registered["nickname"] == ""
            with socket.create_connection(("127.0.0.1",game.port), timeout=8) as sock:
                verified = call(sock,"VerifyLoginToken",1,login_token=registered["ticket"])
                assert verified.status == pb.StatusCode_OK
                pid = verified.user_id
                signin = call(sock,"PlayerLogin",2)
                assert not signin.player_name and signin.scene_id == 100
                assert call(sock,"PlayerMainData",3).status == pb.StatusCode_BAD_REQ
                assert call(sock,"ChangeNickName",4,nick_name="风车队长").status == pb.StatusCode_OK
                main, notice = join(sock)
                assert main.scene_id == notice.data.scene_id == 100 and not main.quest_detail.quests
                actor = notice.data.players[0].team.char1
                assert (actor.pos.x, actor.pos.y, actor.pos.z, actor.rot.y) == (-22,916,982,18630)
                assert call(sock,"AcceptQuest",75,quest_type=pb.EQuestType_MainQuest,quest_id=1000001).status == pb.StatusCode_BAD_REQ
                assert phase(sock,1,"操作指引") == pb.StatusCode_OK
                assert phase(sock,1,"wrong") == pb.StatusCode_BAD_REQ
                # Minimal protobuf for TutorialReq 1589: field 1 = 100, no optional label.
                sock.sendall(encode(MsgId.TutorialReq, b"\x08\x64", 140))
                assert next_msg(sock,MsgId.TutorialRsp,pb.TutorialRsp).status == pb.StatusCode_OK
                stars = [1,2,3,4,9,10,12,13,18,19,20,21,23,24,25,26,27,28,29,30,31,32,33,38,39,45]
                for i, n in enumerate(stars, start=100):
                    assert phase(sock,i,f"收集星星{n}") == pb.StatusCode_OK
                for i,n in ((126,1),(144,19),(145,20)):
                    assert phase(sock,i,f"击败boss后_星星{n}") == pb.StatusCode_OK
                # The requested numeric point remains authoritative when the text differs.
                assert phase(sock,100,"收集星星2") == pb.StatusCode_OK
                for _ in range(17):
                    assert phase(sock,125,"收集星星45") == pb.StatusCode_OK
                assert phase(sock,127,"击败boss后_星星2") == pb.StatusCode_BAD_REQ
                assert not db_value(pid,100000021)
                assert call(sock,"AreaUnlock",18,area_id=1001).status == pb.StatusCode_BAD_REQ
                assert call(sock,"AreaClose",19,area_id=101).area.area_state == pb.AreaData.Close
                device = call(sock,"AreaUnlock",20,area_id=101)
                assert device.status == pb.StatusCode_OK and device.area.area_id == 101 and device.area.area_state == pb.AreaData.Unlock
                assert device.area.level == 1 and not device.area.items
                assert phase(sock,2,"区域装置激活") == pb.StatusCode_OK
                assert phase(sock,3,"跳跃引导") == pb.StatusCode_OK
                crystal = call(sock,"Collecting",21,item_id=10800001)
                assert crystal.status == pb.StatusCode_OK and crystal.collections[0].type == 8
                assert call(sock,"Collecting",22,item_id=10800001).status == pb.StatusCode_ALREADY_COLLECTED
                assert phase(sock,4,"收集水晶1") == pb.StatusCode_OK
                open_chest(sock,0)
                assert phase(sock,5,"打开宝箱1") == pb.StatusCode_OK
                move = pb.PlayerSceneRecordReq()
                move.data.char_recorder_data_lst.add(char_id=101001).pos.CopyFrom(pb.Vector3(x=75,y=112,z=145))
                sock.sendall(encode(MsgId.PlayerSceneRecordReq,move.SerializeToString(),70))
                next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            game.stop()
            game.start()
            code, signed = post("login",first)
            assert code == 200
            sock, info, again = login(game.port,signed["ticket"])
            with sock:
                main, scene = join(sock)
                assert info.scene_id == main.scene_id == scene.data.scene_id == 100 and again == pid
                assert scene.data.players[0].team.char1.pos.x == 75
                assert any(area.area_id==101 and area.area_state==pb.AreaData.Unlock for area in scene.data.areas)
                assert any(box.index==0 and box.box_id==30001 for box in scene.data.treasure_boxes)
                assert phase(sock,5) == pb.StatusCode_OK
                assert phase(sock,6,"攻击引导") == pb.StatusCode_OK
                assert call(sock,"Collecting",23,item_id=10800002).status == pb.StatusCode_OK
                assert phase(sock,7,"收集水晶2") == pb.StatusCode_OK
                open_chest(sock,1)
                # The native reward endpoint supplies the two authored combat drop boxes. The client
                # owns combat and its animation; this fixture verifies only the server-side rewards.
                for reward_id, amount in ((1001,100),(1002,1000)):
                    before = db_value(pid,101)
                    reply = call(sock,"GamePlayReward",80,reward_id=reward_id,num=1)
                    assert reply.status == pb.StatusCode_OK and reply.dynamic_treasure_box_info.box_id == reward_id+2000
                    open_chest(sock,reply.dynamic_treasure_box_info.index,pb.TREASURE_BOX_TYPE_DYNAMIC)
                    assert db_value(pid,101) == before+amount
                assert phase(sock,8,"技能引导") == pb.StatusCode_OK
                assert db_value(pid,100000021) == 0
                assert phase(sock,9,"交互风车离开空间") == pb.StatusCode_OK
                assert phase(sock,9) == pb.StatusCode_OK and db_value(pid,100000021) == 1
                assert call(sock,"ChangeSceneChannel",24,scene_id=9999).status == pb.StatusCode_OK
                assert next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data.scene_id == 9999
                assert phase(sock,10,"出现开放空间字幕") == pb.StatusCode_OK
                assert phase(sock,6) == pb.StatusCode_OK  # retransmit after scene switch
                assert phase(sock,6,"wrong") == pb.StatusCode_BAD_REQ
                assert not call(sock,"GamePlayReward",25,reward_id=1003,num=1).items and db_value(pid,100000021)==1
                assert call(sock,"AcceptQuest",26,quest_type=pb.EQuestType_MainQuest,quest_id=1000001).quest.quest_id==100011
            peer_ticket = post("register",second)[1]["ticket"]
            peer,peer_info,peer_id = login(game.port,peer_ticket)
            with peer:
                _, peer_scene = join(peer)
                assert peer_info.scene_id == peer_scene.data.scene_id == 100
                assert not peer_scene.data.areas and not peer_scene.data.treasure_boxes and not db_value(peer_id,100000021)
            game.stop()
            # A role that predates persisted locations must retain its historical plaza fallback, like other old saves.
            with closing(sqlite3.connect(root/"game.sqlite")) as conn, conn:
                counts = dict(conn.execute("SELECT player_id, count(*) FROM player_kv WHERE ns='tutorial_stars' GROUP BY player_id"))
                assert counts == {pid: 29}
                conn.execute("INSERT INTO player_kv VALUES(?,'tutorial','ids',?)",(peer_id,b"9"))
                conn.execute("DELETE FROM private_locations WHERE player_id=?",(peer_id,))
                conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='profile' AND k='bottle_intro'",(peer_id,))
            game.start()
            for account, expected in ((first,pid),(second,peer_id)):
                sock, info, role = login(game.port,post("login",{k:account[k] for k in ("username","password")})[1]["ticket"])
                with sock:
                    main, scene = join(sock)
                    assert role == expected and info.scene_id == main.scene_id == scene.data.scene_id == 9999
                    if role == pid:
                        assert phase(sock,9)==pb.StatusCode_OK and db_value(pid,100000021)==1
                        assert phase(sock,145,"击败boss后_星星20") == pb.StatusCode_OK
                        assert phase(sock,125) == pb.StatusCode_OK
                        with closing(sqlite3.connect(root / "game.sqlite")) as db:
                            records = db.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='tutorial_stars'",(pid,)).fetchall()
                            assert len(records) == 29 and all(int(value)==1 for _,value in records)
                    else:
                        # Imported aggregate-only receipt must not authorize another starter award.
                        assert phase(sock,9)==pb.StatusCode_OK and not db_value(peer_id,100000021)
                        assert phase(sock,4,"收集水晶1") == pb.StatusCode_BAD_REQ
    finally:
        game.stop()
    print("PASS native scene 100, 29 star IDs, optional text, duplicate staircase reports/relogin, crystals/chests, windmill, plaza transfer, single reward/quest and unchanged old roles.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
