"""Verify actual 1.1.12 prologue telemetry over TCP without granting or finishing anything."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from test_game_realm import join,login
from test_observed_initialization import call


def run(root,resources):
    ids=LocalIdentity(root/"identity.sqlite3")
    a,b=ids.create_profile("引导甲"),ids.create_profile("引导乙")
    game=LocalGameService(ids,root,resources,port=0)
    def enter(profile):
        s,_,pid=login(game.port,ids.issue_ticket(*profile))
        main,_=join(s)
        return s,pid,main
    def files(pid):
        with closing(sqlite3.connect(root/"game.sqlite")) as db:
            return (db.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='tutorial' ORDER BY k",(pid,)).fetchall(),
                    db.execute("SELECT item_id,item_detail_blob FROM items WHERE player_id=? ORDER BY item_id",(pid,)).fetchall())
    def report(c,n,name):
        return call(c,"Tutorial",tutorial_id=n,tutorial_name=name).status
    game.start()
    try:
        c,pid,_=enter(a)
        with c:
            assert report(c,11,"菲对话开始")==pb.StatusCode_BAD_REQ
            call(c,"AcceptQuest",quest_type=pb.EQuestType_MainQuest,quest_id=1000001)
            before=files(pid)[1]
            assert report(c,12,"菲对话结束")==pb.StatusCode_BAD_REQ
            assert report(c,11,"unknown")==pb.StatusCode_BAD_REQ
            assert report(c,11,"菲对话开始")==pb.StatusCode_OK
            assert report(c,11,"菲对话开始")==pb.StatusCode_OK
            assert report(c,12,"菲对话结束")==pb.StatusCode_OK
            assert report(c,9,"交互风车离开空间")==pb.StatusCode_BAD_REQ
            assert files(pid)==([("11",1),("12",1),("ids",b"11,12")],before)
        d,other,_=enter(b)
        with d:
            assert report(d,12,"菲对话结束")==pb.StatusCode_BAD_REQ
            assert not files(other)[0]
        game.stop();game.start()
        c,after,main=enter(a)
        with c:
            assert after==pid and report(c,12,"菲对话结束")==pb.StatusCode_OK
            quest=next(q for q in main.quest_detail.quests if q.quest_id==100011)
            assert quest.status==pb.QuestStatus_InProgress and quest.conditions[0].progress==0
            assert files(pid)[0]==[("11",1),("12",1),("ids",b"11,12")]
    finally:game.stop()
    print("PASS native Faye dialogue telemetry 11/12, idempotence, correct quest on reconnect, no rewards, account isolation.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as t:
        run(Path(t),p.parse_args().resources)
