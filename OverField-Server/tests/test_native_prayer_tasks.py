"""Real CN prayer task IDs must have matching native quest states, inventory awards and rollover."""
import argparse
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb,MsgId
from local_game_wire import encode,receive
from private_chat import NEAR
from local_identity import LocalIdentity
from private_dungeons import _table,reward_items
import private_bless_tasks as prayer
from private_shop import CN,balance
from test_game_realm import join,login,next_msg
from test_observed_initialization import call
from utils import db


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    accounts=[identity.create_profile("日任务甲"),identity.create_profile("日任务乙")]
    host=LocalGameService(identity,root,resources,port=0)
    clock=[datetime(2026,11,25,4,58,tzinfo=CN)]
    fixed=[522002,521004,522001,522003]
    def open_role(n):
        client,info,pid=login(host.port,identity.issue_ticket(*accounts[n]))
        main,_=join(client)
        return client,pid,main
    def tagged(main):
        return {q.quest_id:q for q in main.quest_detail.quests if q.quest_id in prayer.catalog()}
    def take(sock,qid=522002,kind=7):
        return call(sock,"ClaimQuestReward",quest_id=qid,quest_type=kind)
    def units(pid,item):
        stock=balance(pid,item)
        return stock.main_item.base_item.num if stock else 0
    def estate(pid,qid):
        return pb.Quest.FromString(db.get_quest(pid,qid)).status
    def wallet(pid):
        return units(pid,102),units(pid,125)
    def emotion(sock):
        req=pb.SendChatMsgReq(type=NEAR,expression=101)
        sock.sendall(encode(MsgId.SendChatMsgReq,req.SerializeToString(),81))
        ready=None
        acknowledged=False
        for _ in range(200):
            header,raw=receive(sock)
            if header.msg_id==MsgId.QuestNotice:
                ready=next((q for q in pb.QuestNotice.FromString(raw).quests if q.quest_id==522001),ready)
            if header.packet_id==81 and header.msg_id==MsgId.SendChatMsgRsp:
                assert pb.SendChatMsgRsp.FromString(raw).status==pb.StatusCode_OK
                acknowledged=True
            if acknowledged and ready is not None:return ready
        raise AssertionError("missing native quest completion")
    def pending(quest):
        return (quest.status==pb.QuestStatus_Completed and quest.complete_count==0 and
                len(quest.conditions)==1 and quest.conditions[0].status==pb.QuestStatus_Completed)
    with patch.object(prayer,"now",side_effect=lambda:clock[0]):
        host.start()
        try:
            a,aid,main=open_role(0)
            with closing(a):
                assert not main.daily_task.tasks and not tagged(main)
                assert take(a).status==pb.StatusCode_Quest_Not_Finish
                assert len(prayer.catalog())==45
                assert len(prayer.choose(aid,"2026-11-24"))==len(set(prayer.choose(aid,"2026-11-24")))==4
                assert reward_items(aid,2000600)=={102:30,125:2}
                # A synthetic completion of the original chapter step on a real logged-in role.
                with host.lock,db.conn,patch.object(prayer,"choose",return_value=fixed):
                    from private_first_quest import save
                    from utils.pb_create import make_Quest
                    session=host.active[accounts[0][0]]
                    gate=make_Quest(aid,prayer.UNLOCK);gate.status=pb.QuestStatus_Finish
                    save(session,gate)
                    session.publish()
                assignment=next_msg(a,MsgId.DailyTaskUpdateNotice,pb.DailyTaskUpdateNotice)
                assert dict(assignment.tasks)==dict(enumerate(fixed,1))
                assert take(a).status==pb.StatusCode_Quest_Not_Finish
            b,bid,locked=open_role(1)
            with closing(b):
                assert not locked.daily_task.tasks
                assert take(b).status==pb.StatusCode_Quest_Not_Finish
            a,aid,main=open_role(0)
            with closing(a):
                assert list(sorted(main.daily_task.tasks.values()))==sorted(fixed)
                assert set(tagged(main))==set(fixed)
                assert all(q.status==pb.QuestStatus_InProgress and q.conditions for q in tagged(main).values())
                start=wallet(aid)
                assert take(a,kind=9).status!=pb.StatusCode_OK
                assert call(a,"ChangeSceneChannel",scene_id=9999,channel_label=1).status==pb.StatusCode_OK
                assert estate(aid,522002)==pb.QuestStatus_InProgress
                assert call(a,"ChangeSceneChannel",scene_id=9999,channel_label=2).status==pb.StatusCode_OK
                assert estate(aid,522002)==pb.QuestStatus_Completed
                done=emotion(a)
                assert pending(done) and done.conditions[0].progress==1
                assert wallet(aid)==start
                # Simulate a pre-fix save where server marked the condition claimed ahead of its parent.
                with host.lock,db.conn:
                    legacy=pb.Quest.FromString(db.get_quest(aid,522002))
                    assert pending(legacy)
                    legacy.conditions[0].status=pb.QuestStatus_Finish
                    db.set_quest(aid,522002,legacy.SerializeToString())
            host.stop();host.start()
            a,aid,main=open_role(0)
            with closing(a):
                available={qid for qid,q in tagged(main).items() if pending(q)}
                assert available=={522001,522002} and wallet(aid)==start
                assert all(tagged(main)[qid].conditions[0].progress==1 for qid in available)
                # The native button enumerates all type-7 tasks that still have condition state 2.
                rsp=take(a)
                assert rsp.status==pb.StatusCode_OK and rsp.quest.status==pb.QuestStatus_Finish
                assert rsp.quest.conditions[0].status==pb.QuestStatus_Finish and rsp.quest.complete_count==1
                assert {i.main_item.item_id:i.main_item.base_item.num for i in rsp.items}=={102:30,125:2}
                assert wallet(aid)==(start[0]+30,start[1]+2)
                assert take(a).status==pb.StatusCode_REPEAT_GET_REWARD
                assert take(a,522001).status==pb.StatusCode_OK
                assert take(a,522001).status==pb.StatusCode_REPEAT_GET_REWARD
                assert wallet(aid)==(start[0]+60,start[1]+4)
                # Currency goals accumulate the real deductions, ignore no-ops and cap at the authored amount.
                with host.lock,db.conn:
                    for n in (11,2988,0,1):
                        stock=balance(aid,101);stock.main_item.base_item.num-=n
                        db.set_item_detail(aid,stock.SerializeToString(),101)
                    assert estate(aid,521004)==pb.QuestStatus_Completed
                    assert db.get_quest(bid,521004) is None
                clock[0]+=timedelta(minutes=4)
                with patch.object(prayer,"choose",return_value=fixed):
                    call(a,"PlayerPing")
                # The native server committed the ping update before pushing frames to the socket.
                with host.lock:
                    day,ids=prayer.selected(aid)
                    assert day=="2026-11-25" and len(set(ids))==4
                    assert wallet(aid)==(start[0]+60,start[1]+4)
                mails=call(a,"GetMails")
                mail=next(m for m in mails.mails if m.model_id==20)
                assert {i.item_id:i.num for i in mail.items}=={102:30,125:2}
                assert sum(m.model_id==20 for m in call(a,"GetMails").mails)==1
            host.stop();host.start()
            a,_,after=open_role(0)
            with closing(a):
                assert set(after.daily_task.tasks.values())==set(ids)==set(tagged(after))
                assert sum(m.model_id==20 for m in call(a,"GetMails").mails)==1
                assert not db.conn.execute("SELECT 1 FROM mail WHERE player_id=? AND mail_id=?",(bid,mail.mail_id)).fetchone()
                assert pending(emotion(a))
            host.stop()
            clock[0]+=timedelta(days=1)
            host.start()
            a,_,_=open_role(0)
            with closing(a):
                mails=[m for m in call(a,"GetMails").mails if m.model_id==20]
                assert len(mails)==2 and all({i.item_id:i.num for i in m.items}=={102:30,125:2} for m in mails)
                assert wallet(aid)==(start[0]+60,start[1]+4)
        finally:
            host.stop()
    print("PASS: four original tasks and chapter gate, actual channel objective, authoritative wallet progress, 30 stars + 2 stones, replay, role isolation, 05:00 mail and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:run(Path(tmp),args.resources)
