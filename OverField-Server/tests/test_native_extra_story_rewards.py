"""Native extra-story rewards: authored final interactions, 24 configured settlements, two accounts."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_dungeons import _table
from private_extra_story_rewards import terminals
from private_first_quest import saved
from test_game_realm import join_after_intro, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest


INCOME = {1:350, 102:50, 101:3000, 192:5}


def run(root, resources):
    ident = LocalIdentity(root / "identity.sqlite3")
    profiles = [ident.create_profile(n) for n in ("额外故事甲","额外故事乙")]
    game = LocalGameService(ident,root,resources,port=0)

    def connect(profile):
        sock, _, pid = login(game.port, ident.issue_ticket(*profile))
        main, _ = join_after_intro(sock)
        return sock, pid, main

    def claim(sock, qid):
        return call(sock,"ClaimQuestReward",70,quest_type=pb.EQuestType_ExtraQuest,quest_id=qid)

    def stage(pid, qid, status):
        q = make_Quest(pid,qid)
        q.status = status
        for c in q.conditions:
            if status != pb.QuestStatus_InProgress:
                c.status = pb.QuestStatus_Finish
                c.progress = _table("Achieve","achieve")[c.condition_id]["count_param"]
        q.complete_count = int(status == pb.QuestStatus_Finish)
        db.set_quest(pid,qid,q.SerializeToString())

    def event(sock, pid, qid):
        quest = saved(pid,qid)
        assert quest.status == pb.QuestStatus_InProgress and len(quest.conditions)==1
        key = quest.conditions[0].condition_id
        goal = _table("Achieve","achieve")[key]
        kind = goal["new_achieve_condition"]
        assert kind in (37,40)
        if kind==37:
            payload=pb.NpcTalkReq(talk_type=pb.NpcTalkReq.NpcTalkType_Npc,id=key)
        else:
            payload=pb.NpcTalkReq(talk_type=pb.NpcTalkReq.NpcTalkType_Mechanism,id=goal["param"][0])
        sock.sendall(encode(MsgId.NpcTalkReq,payload.SerializeToString(),44))
        changes = {}
        for _ in range(200):
            h, data=receive(sock)
            if h.msg_id==MsgId.QuestNotice:
                changes.update({q.quest_id:q for q in pb.QuestNotice.FromString(data).quests})
            if h.msg_id==MsgId.NpcTalkRsp and h.packet_id==44:
                assert pb.NpcTalkRsp.FromString(data).status == pb.StatusCode_OK
                break
        assert qid in changes
        return changes

    def inventory(player):
        with game.lock:
            balance = {}
            for item in INCOME:
                raw=db.get_item_detail(player,item)
                balance[item]=pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
            return balance

    def paid(sock, pid, tail):
        before=inventory(pid)
        reply=claim(sock,tail)
        assert reply.status==pb.StatusCode_OK and reply.quest.quest_id==tail
        assert reply.quest.status==pb.QuestStatus_Finish and reply.quest.complete_count==1
        assert {it.main_item.item_id:it.main_item.base_item.num for it in reply.items}==INCOME
        assert {k:inventory(pid)[k]-v for k,v in before.items()}==INCOME
        assert claim(sock,tail).status==pb.StatusCode_REPEAT_GET_REWARD

    game.start()
    try:
        a, pid, _=connect(profiles[0])
        b, peer, _=connect(profiles[1])
        with a,b:
            cfg=terminals()
            assert len(cfg)==24 and sum(len(v[0]) for v in cfg.values())==100
            assert {v[1] for v in cfg.values()}=={2000500}
            for invalid in (0,200001,200031,987654321):
                assert claim(a,invalid).status==pb.StatusCode_BAD_REQ
            initial=inventory(pid)
            for tail,(steps,_) in cfg.items():
                assert claim(a,tail).status==pb.StatusCode_Quest_Not_Finish
                with game.lock,db.conn:
                    if tail==220012:
                        # Only acceptance is staged; the native NPC reports advance both original steps.
                        stage(pid,steps[0],pb.QuestStatus_InProgress)
                    else:
                        for key in steps[:-1]:
                            stage(pid,key,pb.QuestStatus_Finish)
                        stage(pid,tail,pb.QuestStatus_InProgress)
                if tail==220012:
                    update=event(a,pid,steps[0])
                    assert update[steps[0]].status==pb.QuestStatus_Finish and update[tail].status==pb.QuestStatus_InProgress
                assert claim(a,tail).status==pb.StatusCode_Quest_Not_Finish
                ready=event(a,pid,tail)[tail]
                assert ready.status==pb.QuestStatus_Completed and not ready.complete_count
                if tail!=220061:
                    paid(a,pid,tail)
                assert claim(b,tail).status==pb.StatusCode_Quest_Not_Finish
            assert {k:inventory(pid)[k]-v for k,v in initial.items()}=={k:23*n for k,n in INCOME.items()}
            # A legacy final state alone is enough to prevent a second payment.
            with game.lock,db.conn:
                stage(peer,200007,pb.QuestStatus_Finish)
            assert claim(b,200007).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        game.stop()
    game.start()
    try:
        a,pid,main=connect(profiles[0])
        b,peer,_=connect(profiles[1])
        with a,b:
            states={q.quest_id:q.status for q in main.quest_detail.quests}
            assert states[220061]==pb.QuestStatus_Completed and states[220012]==pb.QuestStatus_Finish
            paid(a,pid,220061)
            assert claim(a,220012).status==pb.StatusCode_REPEAT_GET_REWARD
            assert claim(b,220061).status==pb.StatusCode_Quest_Not_Finish
            assert {k:inventory(pid)[k]-v for k,v in initial.items()}=={k:24*n for k,n in INCOME.items()}
            assert db.conn.execute("SELECT COUNT(*) FROM claimed_rewards WHERE player_id=? AND system='extra_story'",(pid,)).fetchone()[0]==24
    finally:
        game.stop()
    print("PASS all 24 extra-story final NPC/mechanism events, paid 2->3 transitions, prior/withdrawn/duplicate, saved ready state, balances and isolation.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
