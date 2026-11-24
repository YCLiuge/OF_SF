"""Play all six objectives of 初入空间 on a temporary persistent realm."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login, next_msg


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profile=identity.create_profile("芳汀剧情")
    game=LocalGameService(identity,root,resources,port=0)
    game.start()
    def request(sock,name,**fields):
        raw=getattr(pb,name+"Req")(**fields).SerializeToString()
        sock.sendall(encode(getattr(MsgId,name+"Req"),raw,29))
        for _ in range(256):
            hdr,raw=receive(sock)
            if hdr.msg_id==getattr(MsgId,name+"Rsp") and hdr.packet_id==29:
                return getattr(pb,name+"Rsp").FromString(raw)
        raise AssertionError(name)
    def movement(sock,x,z):
        record=pb.PlayerSceneRecordReq()
        point=record.data.char_recorder_data_lst.add(char_id=101001).pos
        point.x,point.z=x,z
        sock.sendall(encode(MsgId.PlayerSceneRecordReq, record.SerializeToString(),30))
        next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
    def connect():
        sock,*_=login(game.port,identity.issue_ticket(*profile))
        data,_=join_after_intro(sock)
        return sock,{q.quest_id:q.status for q in data.quest_detail.quests}
    def accept(sock,story):
        return request(sock,"AcceptQuest",quest_type=pb.EQuestType_MainQuest,quest_id=story)
    def interact(sock,resource):
        return request(sock,"NpcTalk",id=resource,talk_type=pb.NpcTalkReq.NpcTalkType_Mechanism)
    try:
        sock,_=connect()
        with sock:
            assert accept(sock,1000002).status==pb.StatusCode_BAD_REQ
            assert accept(sock,1000001).quest.quest_id==100011
            movement(sock,1992,-2666)
            assert request(sock,"NpcTalk",id=11000111).status==pb.StatusCode_OK
            movement(sock,2570,-2187)
            assert request(sock,"NpcTalk",id=11000121).status==pb.StatusCode_OK
            assert request(sock,"Gacha",gacha_id=2000,is_single=True).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100021
            assert interact(sock,1000211).status==pb.StatusCode_BAD_REQ
            movement(sock,1503,-1787)
            assert request(sock,"NpcTalk",id=11000211).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000211).status==pb.StatusCode_OK
            assert interact(sock,1000211).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100022
            assert interact(sock,1000221).status==pb.StatusCode_BAD_REQ
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert interact(sock,1000221).status==pb.StatusCode_OK
            assert interact(sock,1000221).status==pb.StatusCode_OK
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100023
        game.stop()
        game.start()
        sock,quests=connect()
        with sock:
            assert {k:quests[k] for k in (100021,100022,100023)}=={100021:3,100022:3,100023:1}
            assert 100024 not in quests
            assert request(sock,"AreaUnlock",area_id=1002).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000241).status==pb.StatusCode_BAD_REQ
            assert request(sock,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert accept(sock,1000002).quest.quest_id==100023
            activated=request(sock,"AreaUnlock",area_id=1001)
            assert activated.status==pb.StatusCode_OK and activated.area.area_state==pb.AreaData.Unlock
            assert {i.item_id:i.num for i in activated.area.items}=={1:300,102:10,101:1000}
            assert accept(sock,1000002).quest.quest_id==100024
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
            assert request(sock,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
            movement(sock,-3515,1483)
            assert interact(sock,1000251).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000241).status==pb.StatusCode_BAD_REQ
            movement(sock,-3918,3442)
            assert request(sock,"NpcTalk",id=1000241).status==pb.StatusCode_BAD_REQ
            assert interact(sock,1000241).status==pb.StatusCode_OK
            assert interact(sock,1000241).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100025
            assert interact(sock,1000251).status==pb.StatusCode_BAD_REQ
            movement(sock,-3515,1483)
            assert interact(sock,1000251).status==pb.StatusCode_OK
            assert interact(sock,1000251).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.quest_id==100026
            assert request(sock,"NpcTalk",id=11000261).status==pb.StatusCode_BAD_REQ
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.status==pb.QuestStatus_InProgress
            assert request(sock,"ChangeSceneChannel",scene_id=9999).status==pb.StatusCode_OK
            assert accept(sock,1000002).quest.status==pb.QuestStatus_Finish
            award=request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest)
            assert award.status==pb.StatusCode_OK and award.quest.quest_id==100026
            assert {i.main_item.item_id:i.main_item.base_item.num for i in award.items}=={1:300,101:325,102:50,190:5}
            assert request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"AreaUnlock",area_id=1001).status==pb.StatusCode_BAD_REQ
        game.stop()
        game.start()
        sock,quests=connect()
        with sock:
            assert all(quests.get(q)==pb.QuestStatus_Finish for q in range(100021,100027))
            assert request(sock,"ClaimQuestReward",quest_id=1000002,quest_type=pb.EQuestType_MainQuest).status==pb.StatusCode_REPEAT_GET_REWARD
            assert request(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            scene=next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice)
            device=next(a for a in scene.data.areas if a.area_id==1001)
            assert device.level==1 and device.area_state==pb.AreaData.Unlock
            assert not request(sock,"AreaUnlock",area_id=1001).area.items
        previous = identity.create_profile("旧档芳汀")
        legacy,*_ = login(game.port,identity.issue_ticket(*previous))
        with legacy:
            _,initial=join_after_intro(legacy)
            assert initial.data.scene_id==9999
            assert request(legacy,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            scene=next_msg(legacy,MsgId.SceneDataNotice,pb.SceneDataNotice)
            assert not any(a.area_id==1001 for a in scene.data.areas)
            assert accept(legacy,1000002).status==pb.StatusCode_BAD_REQ
            first=request(legacy,"AreaUnlock",area_id=1001)
            assert first.status==pb.StatusCode_OK and len(first.area.items)==3
            assert request(legacy,"AreaClose",area_id=1001).area.area_state==pb.AreaData.Close
            assert request(legacy,"ChangeSceneChannel",scene_id=9999).status==pb.StatusCode_OK
            assert accept(legacy,1000001).quest.quest_id==100011
            movement(legacy,1992,-2666)
            assert request(legacy,"NpcTalk",id=11000111).status==pb.StatusCode_OK
            movement(legacy,2570,-2187)
            assert request(legacy,"NpcTalk",id=11000121).status==pb.StatusCode_OK
            assert request(legacy,"Gacha",gacha_id=2000,is_single=True).status==pb.StatusCode_OK
            assert accept(legacy,1000002).quest.quest_id==100021
            movement(legacy,1503,-1787)
            assert interact(legacy,1000211).status==pb.StatusCode_OK
            assert request(legacy,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            assert interact(legacy,1000221).status==pb.StatusCode_OK
            assert accept(legacy,1000002).quest.quest_id==100024
            assert not request(legacy,"AreaUnlock",area_id=1001).area.items
    finally:
        game.stop()
    print("PASS six Chapter 00 objectives, one-time device reward, role-specific movement, wrong scene, replay and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:run(Path(state),parser.parse_args().resources)

