"""Play the first chapter to the authored mechanism that opens the first investigation map."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg


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
        data,_=join(sock)
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
    finally:
        game.stop()
    print("PASS authored Fount trigger, actual tutorial recruitment, scene-1 entry, duplicate/out-of-order/invalid dialogs and persistence.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:run(Path(state),parser.parse_args().resources)

