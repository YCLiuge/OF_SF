"""Exercise native chapter acceptance, progress-preserving resumes, admission gates and settlement."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login
from test_native_gacha import call
from private_dungeons import _table
from private_first_quest import saved
from private_shop import CN
from private_story_catalog import catalog, chapter_state
from utils import db
from utils.pb_create import make_Quest


def run(root, resources):
    ids = LocalIdentity(root/"identity.sqlite3")
    users = [ids.create_profile(name) for name in ("目录甲","目录乙")]
    game = LocalGameService(ids,root,resources,port=0)
    game.start()
    sequence = 20

    def connect(profile):
        sock, _, player = login(game.port,ids.issue_ticket(*profile))
        join_after_intro(sock)
        return sock, player

    def request(sock, verb, story, kind=pb.EQuestType_MainQuest):
        nonlocal sequence
        sequence += 1
        return call(sock,verb,sequence,quest_type=kind,quest_id=story)

    def done(player, key):
        quest = make_Quest(player,key)
        for cond in quest.conditions:
            cond.progress = 1
            cond.status = pb.QuestStatus_Finish
        quest.status = pb.QuestStatus_Finish
        quest.complete_count = 1
        db.set_quest(player,key,quest.SerializeToString())

    def balance(player, item):
        raw = db.get_item_detail(player,item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    def put_rewards(player, chapter, keys):
        data = pb.Chapter(chapter_id=chapter,rewarded_story_ids=keys)
        db.set_chapter(player,chapter,data.SerializeToString())

    def set_points(player, scene, points):
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'ach',?,?)",(player,f"exp:{scene}",points))

    try:
        member = catalog()
        assert len(member)==94 and len({c["i_d"] for c,_,_ in member.values()})==9
        assert sum(len(steps) for _,_,steps in member.values())==536
        a,pid = connect(users[0])
        b,other = connect(users[1])
        with a,b:
            for fake in (1000401,1000509,220011,100031,0xffffffff):
                assert request(a,"AcceptQuest",fake).status==pb.StatusCode_BAD_REQ
                assert request(a,"ClaimQuestReward",fake).status==pb.StatusCode_BAD_REQ
            assert request(a,"AcceptQuest",1000003,0).status==pb.StatusCode_BAD_REQ
            assert request(a,"AcceptQuest",1000003).status==pb.StatusCode_BAD_REQ
            assert request(a,"ClaimQuestReward",1000002).status==pb.StatusCode_BAD_REQ
            # Nonzero saved state for the previous story's first step opens this one in the authored client check.
            with game.lock, db.conn:
                q=make_Quest(pid,100021)
                db.set_quest(pid,100021,q.SerializeToString())
            accepted=request(a,"AcceptQuest",1000003)
            assert accepted.status==pb.StatusCode_OK and accepted.quest.quest_id==100031
            with game.lock, db.conn:
                done(pid,100031)
                q=make_Quest(pid,100032)
                q.conditions[0].progress=1
                db.set_quest(pid,100032,q.SerializeToString())
            resume=request(a,"AcceptQuest",1000003)
            assert resume.quest==q and request(a,"AcceptQuest",1000003).quest==q
            assert request(b,"AcceptQuest",1000003).status==pb.StatusCode_BAD_REQ
            _,_,second_steps=member[1000002]
            income={1:300,101:325,102:50,190:5}
            with game.lock, db.conn:
                for key in second_steps[:-1]:
                    done(pid,key)
                before={item:balance(pid,item) for item in income}
            assert request(a,"ClaimQuestReward",1000002).status==pb.StatusCode_BAD_REQ
            with game.lock, db.conn:
                done(pid,second_steps[-1])
            result=request(a,"ClaimQuestReward",1000002)
            assert result.status==pb.StatusCode_OK and result.quest.quest_id==100026
            assert {i.main_item.item_id:i.main_item.base_item.num for i in result.items}==income
            assert request(a,"ClaimQuestReward",1000002).status==pb.StatusCode_REPEAT_GET_REWARD
            with game.lock:
                assert {item:balance(pid,item)-count for item,count in before.items()}==income
                assert list(chapter_state(pid,10000).rewarded_story_ids)==[1000002]
            # The first chapter gate needs every preceding story's receipt and map 1's own point count.
            chapter, _, _ = member[1000101]
            prior=member[1000001][0]["story_list"]
            total=sum(r.get("total",0) for r in _table("Scene","scene_progress")[1]["scene_progress_info"])
            threshold=(10*total+99)//100
            with game.lock, db.conn:
                done(pid,member[1000009][2][0])
                put_rewards(pid,chapter["pre_chapter_unlock"],prior[:-1])
                set_points(pid,1,threshold)
            assert request(a,"AcceptQuest",1000101).status==pb.StatusCode_BAD_REQ
            with game.lock, db.conn:
                put_rewards(pid,10000,prior)
                set_points(pid,1,threshold-1)
                set_points(pid,2,10000)
            assert request(a,"AcceptQuest",1000101).status==pb.StatusCode_BAD_REQ
            with game.lock, db.conn:
                set_points(pid,1,threshold)
            assert request(a,"AcceptQuest",1000101).status==pb.StatusCode_OK
            with game.lock, db.conn:
                done(pid,member[1000109][2][0])
                db.conn.execute("UPDATE players SET level=10 WHERE player_id=?",(pid,))
            assert request(a,"AcceptQuest",1000110).status==pb.StatusCode_BAD_REQ
            with game.lock, db.conn:
                db.conn.execute("UPDATE players SET level=11 WHERE player_id=?",(pid,))
            assert request(a,"AcceptQuest",1000110).status==pb.StatusCode_OK
            with game.lock, db.conn:
                earlier = member[1000606][0]["pre_chapter_unlock"]
                put_rewards(pid,earlier,_table("Story","story_chapter")[earlier]["story_list"])
                done(pid,member[1000605][2][0])
                set_points(pid,1002,10000)
            with patch("private_story_catalog.now",return_value=datetime(2025,5,6,7,59,59,tzinfo=CN)):
                assert request(a,"AcceptQuest",1000606).status==pb.StatusCode_BAD_REQ
            with patch("private_story_catalog.now",return_value=datetime(2025,5,6,8,0,0,tzinfo=CN)):
                assert request(a,"AcceptQuest",1000606).status==pb.StatusCode_OK
            # The second role has synthetic completed objectives; settlement uses real 1725/1726 and reward tables.
            with game.lock, db.conn:
                for _,_,steps in member.values():
                    for key in steps:
                        done(other,key)
            for story,(_,_,steps) in member.items():
                reply=request(b,"ClaimQuestReward",story)
                assert reply.status==pb.StatusCode_OK and reply.quest.quest_id==steps[-1] and reply.items,story
                assert request(b,"ClaimQuestReward",story).status==pb.StatusCode_REPEAT_GET_REWARD,story
        rollback_user = ids.create_profile("目录回滚")
        sock, trial = connect(rollback_user)
        with sock:
            with game.lock, db.conn:
                for key in member[1000002][2]:
                    done(trial,key)
                initial = {item:balance(trial,item) for item in income}
            with patch("private_story_catalog.db.set_chapter", side_effect=ValueError("test chapter persistence")):
                try:
                    request(sock,"ClaimQuestReward",1000002)
                except (OSError,EOFError):
                    pass
                else:
                    raise AssertionError("The interrupted settlement returned success")
        sock, _ = connect(rollback_user)
        with sock:
            with game.lock:
                assert {item:balance(trial,item) for item in income} == initial
                assert not chapter_state(trial,10000).rewarded_story_ids
            assert request(sock,"ClaimQuestReward",1000002).status == pb.StatusCode_OK
        game.stop()
        game.start()
        b,restored=connect(users[1])
        with b:
            assert restored==other
            for chapter in _table("Story","story_chapter").values():
                assert set(chapter_state(restored,chapter["i_d"]).rewarded_story_ids)==set(chapter["story_list"])
            assert request(b,"ClaimQuestReward",1000907).status==pb.StatusCode_REPEAT_GET_REWARD
        a,_=connect(users[0])
        with a:
            assert request(a,"AcceptQuest",1000003).quest==q
            assert request(a,"ClaimQuestReward",1000003).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        game.stop()
    print("PASS native 9 chapters / 94 story settlements and replay; 536 grouped steps, receipts, role isolation, time, level and per-map gates, saved partial progress and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
