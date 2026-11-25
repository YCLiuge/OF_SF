"""Native side quests survive import/undo, while reset-dependent records stay archival."""
import argparse
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import pb
from private_dungeons import _table
from save_import_data import project
from save_import_gameplay import validate
from save_import_fishing import quest_floor, recover_offline
from save_import_quests import HISTORY, add_missing, encoded
from test_game_realm import join, login
from test_native_gacha import call
from test_native_save_import import synthetic, world
from test_private_profiles import request
from utils import db


def run(path,resources):
    with world(path,resources) as (port,game):
        assert quest_floor(401446,3,[[14010446,3,8]])==10
        assert quest_floor(0,3,[[100210904,3,1]])==9
        for qid,cid,progress in ((401447,14010446,8),(0,14010446,8),(401446,14010446,0),
                                 (0,100210901,3),(0,100110701,1)):
            assert quest_floor(qid,3,[[cid,3,progress]])==1
        original=project(synthetic())
        source_id=original.pop("identity")
        quests=_table("Quest","quest")
        groups=_table("Quest","condition_set_group")
        def row(key,status,count):
            phases=groups[quests[key]["condition_set_group_i_d"]]["quest_condition_set"]
            return [key,status,count,[[cid,status,1] for p in phases for cid in p["achieve_condition_i_d"]]]
        custom=[row(301011,3,3),row(401420,1,0),row(230028,3,1),row(500113,2,1),
                row(522003,1,0),row(910111,1,0),row(302011,1,2)]
        bad=row(301031,3,1)
        bad[3][0][0]=0
        original["quests"].extend([*custom,custom[0],bad,[0,3,1,[[0,3,1]]],[401446,3,1,[[14010446,3,8]]]])
        plan,counts=validate(original)
        live={r[0] for r in plan["additional_quests"]}
        archived={r[0] for r in plan[HISTORY]}
        assert live=={301011,401420,230028,401446} and archived=={500113,522003,910111,302011}
        assert plan["fishing_level_floor"]==10
        auth=request(port,"POST","/local/v1/accounts/register",{"username":"import.quests","nickname":"进度检查","password":"local-synthetic-quest-check"})[1]
        def api(action,**body):
            s,r=request(port,"POST","/local/v1/accounts/import-"+action,{"import_ticket":auth["import_ticket"],**body})
            assert s==200,(s,r.get("error"))
            return r
        def apply():
            pre=api("preview",data=original)
            return api("apply",preview_id=pre["preview_id"])
        def histories(pid):
            return {int(k):pb.Quest.FromString(v) for k,v in db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns=?",(pid,HISTORY))}
        apply()
        pid=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(auth["slot"],)).fetchone()[0]
        assert set(histories(pid))==archived and histories(pid)[302011].complete_count==2
        assert pb.Quest.FromString(db.get_quest(pid,301011)).complete_count==3
        assert not db.get_quest(pid,500113)
        from private_crafts import base
        fishing=pb.LIFE_TYPE_Fishing
        assert base(pid,fishing).level==10 and base(pid,pb.LIFE_TYPE_Cooking).level==1
        api("undo")
        assert base(pid,fishing).level==1
        assert not histories(pid) and not db.get_quest(pid,301011)
        apply()
        with db.conn:
            raw=encoded(row(401420,3,1))
            db.set_quest(pid,401420,raw)
            assert add_missing(db.conn,pid,plan)=={"additional_quests":0,HISTORY:0}
            assert db.get_quest(pid,401420)==raw
            # Simulate a sealed account created with the old importer before first login under this release.
            life=base(pid,fishing)
            life.level,life.proficiency_value=2,136
            db.set_life(pid,fishing,life.SerializeToString())
        with closing(login(game.port,auth["ticket"])[0]) as client:
            main,_=join(client)
            visible={q.quest_id:q for q in main.quest_detail.quests}
            assert live<=set(visible) and not archived&set(visible)
            assert visible[401420].status==pb.QuestStatus_Finish
            report=call(client,"GetLifeInfo",23,life_type=fishing)
            assert (report.life_base_info.level,report.life_base_info.proficiency_value)==(10,136)
            repeat=call(client,"ClaimQuestReward",24,quest_type=pb.EQuestType_ExtraQuest,quest_id=230028)
            assert repeat.status!=pb.StatusCode_OK
            public=main.player_label
        with game.slot_available:
            assert game.slot_available.wait_for(lambda:not game.active,timeout=5)
        with db.conn:
            # A second copy of the old level tests the stopped owner repair and repeat runs.
            life=base(pid,fishing)
            life.level=1
            db.set_life(pid,fishing,life.SerializeToString())
        print("PASS native persistent quests, retained original finish count, historic-only snapshots, undo, idempotence, invalid data and repeat claim")
    assert recover_offline(path,resources,public)=={"previous":1,"confirmed_minimum":10,"result":1}
    assert recover_offline(path,resources,public,apply=True)=={"previous":1,"confirmed_minimum":10,"result":10}
    assert recover_offline(path,resources,public,apply=True)=={"previous":10,"confirmed_minimum":10,"result":10}


if __name__=="__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),arg.parse_args().resources)
