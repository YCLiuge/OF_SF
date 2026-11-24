"""Exercise the six native 荧荧 conversations. Only prior chapters are fixture data."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_dungeons import _table, reward_items
from private_first_quest import saved
from private_story_catalog import catalog, chapter_state
from private_story_dialogues import SCENE
from test_game_realm import join_after_intro, login, next_msg
from utils import db
from utils.pb_create import make_Quest


OBJECTIVES = {1000606: (11060611,11060621), 1000608: (11060811,), 1000705: (11070511,11070521,11070531)}


def run(root, resources):
    ids = LocalIdentity(root/"identity.sqlite3")
    accounts = [ids.create_profile("荧荧对话甲"),ids.create_profile("荧荧对话乙")]
    service = LocalGameService(ids,root,resources,port=0)
    service.start()
    serial = 20

    def connect(profile):
        sock,_,pid=login(service.port,ids.issue_ticket(*profile))
        main,scene=join_after_intro(sock)
        assert scene.data.scene_id==SCENE
        return sock,pid,main

    def request(sock,name,**fields):
        nonlocal serial
        serial+=1
        body=getattr(pb,name+"Req")(**fields).SerializeToString()
        sock.sendall(encode(getattr(MsgId,name+"Req"),body,serial))
        notices={}
        for _ in range(256):
            header,data=receive(sock)
            if header.msg_id==MsgId.QuestNotice:
                notices.update({q.quest_id:(q.status,q.complete_count) for q in pb.QuestNotice.FromString(data).quests})
            if header.msg_id==getattr(MsgId,name+"Rsp") and header.packet_id==serial:
                return getattr(pb,name+"Rsp").FromString(data),notices
        raise AssertionError("Missing native response")

    def interact(sock,cond,kind=pb.NpcTalkReq.NpcTalkType_Npc):
        return request(sock,"NpcTalk",id=cond,talk_type=kind)

    def move(sock,x,z,y=144):
        msg=pb.PlayerSceneRecordReq()
        position=msg.data.char_recorder_data_lst.add(char_id=101001).pos
        position.x,position.y,position.z=x,y,z
        sock.sendall(encode(MsgId.PlayerSceneRecordReq,msg.SerializeToString(),19))
        sync=next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
        assert sync.data[0].data[0].char_recorder_data_lst[0].pos==position

    def story(sock,name,story_id):
        return request(sock,name,quest_id=story_id,quest_type=pb.EQuestType_MainQuest)[0]

    def prerequisites(pid,sid):
        chapter,config,_=catalog()[sid]
        previous=_table("Story","story_chapter")[chapter["pre_chapter_unlock"]]
        scene,_=chapter["map_completion_unlock"]
        with service.lock, db.conn:
            record=chapter_state(pid,previous["i_d"])
            for item in previous["story_list"]:
                if item not in record.rewarded_story_ids:
                    record.rewarded_story_ids.append(item)
            db.set_chapter(pid,previous["i_d"],record.SerializeToString())
            db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'ach',?,?)",(pid,f"exp:{scene}",10000))
            prior=catalog()[config["pre_story_unlock"]][2][0]
            if not saved(pid,prior):
                quest=make_Quest(pid,prior)
                db.set_quest(pid,prior,quest.SerializeToString())

    try:
        sock,pid,_=connect(accounts[0])
        peer,_,_=connect(accounts[1])
        with sock,peer:
            move(peer,-2801,-2291)
            for sid,conds in OBJECTIVES.items():
                assert interact(peer,conds[0])[0].status==pb.StatusCode_BAD_REQ
                assert story(sock,"AcceptQuest",sid).status==pb.StatusCode_BAD_REQ
                prerequisites(pid,sid)
                _,cfg,steps=catalog()[sid]
                assert len(steps)==len(conds)
                accepted=story(sock,"AcceptQuest",sid)
                assert accepted.status==pb.StatusCode_OK and accepted.quest.quest_id==steps[0]
                assert story(sock,"ClaimQuestReward",sid).status==pb.StatusCode_BAD_REQ
                assert request(sock,"ChangeSceneChannel",scene_id=1)[0].status==pb.StatusCode_OK
                move(sock,-2801,-2291)
                assert interact(sock,conds[0])[0].status==pb.StatusCode_BAD_REQ
                assert request(sock,"ChangeSceneChannel",scene_id=SCENE)[0].status==pb.StatusCode_OK
                # 梓's real NPC trigger cannot complete a conversation assigned to 荧荧.
                move(sock,1992,-2666)
                assert interact(sock,conds[0])[0].status==pb.StatusCode_BAD_REQ
                move(sock,-2801,-2291,y=1500)
                assert interact(sock,conds[0])[0].status==pb.StatusCode_BAD_REQ
                move(sock,-2801,-2291)
                if len(conds)>1:
                    assert interact(sock,conds[-1])[0].status==pb.StatusCode_BAD_REQ
                if sid==1000606:
                    assert interact(sock,11060811)[0].status==pb.StatusCode_BAD_REQ
                assert interact(sock,conds[0],pb.NpcTalkReq.NpcTalkType_Mechanism)[0].status==pb.StatusCode_BAD_REQ
                for alias in _table("Achieve","achieve")[conds[0]]["param"]:
                    for kind in (pb.NpcTalkReq.NpcTalkType_Npc,pb.NpcTalkReq.NpcTalkType_Mechanism):
                        assert interact(sock,alias,kind)[0].status==pb.StatusCode_BAD_REQ
                for index,(cond,qid) in enumerate(zip(conds,steps)):
                    reply,notices=interact(sock,cond)
                    assert reply.status==pb.StatusCode_OK and notices[qid]==(pb.QuestStatus_Finish,1)
                    if index+1<len(steps):
                        assert notices[steps[index+1]]==(pb.QuestStatus_InProgress,0)
                        assert story(sock,"AcceptQuest",sid).quest.quest_id==steps[index+1]
                    replay,updates=interact(sock,cond)
                    assert replay.status==pb.StatusCode_OK and not updates
                    restored=saved(pid,qid)
                    assert restored.complete_count==1 and restored.conditions[0].progress==1
                claim=story(sock,"ClaimQuestReward",sid)
                assert claim.status==pb.StatusCode_OK and claim.quest.quest_id==steps[-1]
                expected=reward_items(pid,cfg["reward"])
                assert {i.main_item.item_id:i.main_item.base_item.num for i in claim.items}==expected
                assert story(sock,"ClaimQuestReward",sid).status==pb.StatusCode_REPEAT_GET_REWARD
                assert story(peer,"ClaimQuestReward",sid).status==pb.StatusCode_BAD_REQ
        service.stop()
        service.start()
        sock,again,main=connect(accounts[0])
        with sock:
            assert again==pid
            finished={q.quest_id:q for q in main.quest_detail.quests}
            for sid in OBJECTIVES:
                ch,_,steps=catalog()[sid]
                assert all(finished[q].status==pb.QuestStatus_Finish and finished[q].complete_count==1 for q in steps)
                assert list(chapter_state(pid,ch["i_d"]).rewarded_story_ids).count(sid)==1
                assert story(sock,"ClaimQuestReward",sid).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        service.stop()
    print("PASS six native NPC objectives, three full story settlements, actor/scene/order guards, retries, role isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
