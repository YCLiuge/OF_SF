"""Run each catalogued scene-entry objective against its authored destination on the native private socket."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from private_first_quest import saved
from private_story_catalog import catalog
from test_game_realm import join_after_intro, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest


def run(root, resources):
    ids = LocalIdentity(root/"identity.sqlite3")
    profile = ids.create_profile("场景任务")
    game = LocalGameService(ids, root, resources, port=0)
    game.start()
    try:
        sock, _, pid = login(game.port, ids.issue_ticket(*profile))
        with sock:
            join_after_intro(sock)
            cases = []
            for sid, (_, _, steps) in catalog().items():
                for index, key in enumerate(steps):
                    q = make_Quest(pid, key)
                    for cond in q.conditions:
                        definition = _table("Achieve","achieve")[cond.condition_id]
                        if definition.get("new_achieve_condition") == 39:
                            dest, = definition["param"]
                            assert len(q.conditions) == 1 and definition["count_param"] == 1
                            cases.append((sid, steps, index, dest))
            assert len(cases) == 62
            checked = 0
            for sid, steps, index, dest in cases:
                if sid == 1000002:
                    # Its entire six-step native sequence is exercised in test_story_entry.py.
                    continue
                key = steps[index]
                source = 2 if dest == 1 else 1
                assert call(sock,"ChangeSceneChannel",10,scene_id=source).status == pb.StatusCode_OK
                with game.lock, db.conn:
                    # This role starts each case at the configured step. No other quest is in progress.
                    db.conn.execute("DELETE FROM quests WHERE player_id=?", (pid,))
                    q = make_Quest(pid, key)
                    db.set_quest(pid, key, q.SerializeToString())
                cond = q.conditions[0].condition_id
                call(sock,"NpcTalk",11,id=cond)
                call(sock,"NpcTalk",12,id=dest,talk_type=pb.NpcTalkReq.NpcTalkType_Mechanism)
                assert call(sock,"ChangeSceneChannel",13,scene_id=source).status == pb.StatusCode_OK
                assert saved(pid,key) == q
                assert call(sock,"ChangeSceneChannel",14,scene_id=dest).status == pb.StatusCode_OK
                with game.lock:
                    completed = saved(pid,key)
                    assert completed.status == pb.QuestStatus_Finish and completed.complete_count == 1
                    assert completed.conditions[0].condition_id == cond and completed.conditions[0].progress == 1
                    if index+1 < len(steps):
                        following = saved(pid,steps[index+1])
                        assert following and following.status == pb.QuestStatus_InProgress
                assert call(sock,"ChangeSceneChannel",15,scene_id=dest).status == pb.StatusCode_OK
                assert saved(pid,key) == completed
                checked += 1
            assert checked == 61
        game.stop()
        game.start()
        sock, _, same = login(game.port, ids.issue_ticket(*profile))
        with sock:
            main, _ = join_after_intro(sock)
            assert same == pid and any(q == completed for q in main.quest_detail.quests)
    finally:
        game.stop()
    print("PASS 61 original scene-entry steps over native TCP, authored successor, unrelated interactions, same-scene replays and restart.")


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as state:
        run(Path(state), args.parse_args().resources)
