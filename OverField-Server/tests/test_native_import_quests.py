"""Native side quests survive import/undo, while reset-dependent records stay archival."""
import argparse
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import pb
from private_dungeons import _table
from save_import_data import project
from save_import_gameplay import validate
from save_import_quests import HISTORY, add_missing, encoded
from test_game_realm import join, login
from test_native_gacha import call
from test_native_save_import import synthetic, world
from test_private_profiles import request
from utils import db


def run(path,resources):
    with world(path,resources) as (port,game):
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
        original["quests"].extend([*custom,custom[0],bad,[0,3,1,[[0,3,1]]]])
        plan,counts=validate(original)
        live={r[0] for r in plan["additional_quests"]}
        archived={r[0] for r in plan[HISTORY]}
        assert live=={301011,401420,230028} and archived=={500113,522003,910111,302011}
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
        api("undo")
        assert not histories(pid) and not db.get_quest(pid,301011)
        apply()
        with db.conn:
            raw=encoded(row(401420,3,1))
            db.set_quest(pid,401420,raw)
            assert add_missing(db.conn,pid,plan)=={"additional_quests":0,HISTORY:0}
            assert db.get_quest(pid,401420)==raw
        with closing(login(game.port,auth["ticket"])[0]) as client:
            main,_=join(client)
            visible={q.quest_id:q for q in main.quest_detail.quests}
            assert live<=set(visible) and not archived&set(visible)
            assert visible[401420].status==pb.QuestStatus_Finish
            repeat=call(client,"ClaimQuestReward",24,quest_type=pb.EQuestType_ExtraQuest,quest_id=230028)
            assert repeat.status!=pb.StatusCode_OK
        print("PASS native persistent quests, retained original finish count, historic-only snapshots, undo, idempotence, invalid data and repeat claim")


if __name__=="__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),arg.parse_args().resources)
