"""Exercise material, coin and equipment sweeps through the national PC packet dispatcher."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item
from private_dungeon_sweep import drop_plan
from private_dungeons import _table, load
from private_arcade_wallet import wallet


def run(root, resources):
    users = LocalIdentity(root/"identity.sqlite3")
    profile = users.create_profile("扫荡测试")
    host = LocalGameService(users, root, resources, port=0)
    def connect():
        sock, _, pid = login(host.port, users.issue_ticket(*profile))
        join_after_intro(sock)
        return sock,pid
    def vitality(pid, value):
        db.set_item_detail(pid, make_item(10,value,pid).SerializeToString(),10)
    def qty(response):
        return {i.main_item.item_id:i.main_item.base_item.num for i in response.rewards if i.main_item.HasField("base_item")}
    host.start()
    try:
        sock,pid = connect()
        with sock:
            with host.lock, db.conn:
                configs = [r for r in _table("Dungeon","dungeon").values() if r.get("is_can_sweep")]
                assert len(configs)==152
                for row in configs:
                    stacks,pool,count = drop_plan(row)
                    assert count and 1 in stacks and (len(stacks)>1 or pool), row["i_d"]
                    for template in pool:
                        kind = "Weapon" if _table("Weapon","weapon").get(template) else "Armor"
                        info = _table(kind,kind.lower())[template]
                        prop = _table(kind,kind.lower()+"_property")[info[kind.lower()+"_property_i_d"]]
                        assert any(g["min_level"]<=row["monster_lv"]<=g["max_level"] for g in prop[kind.lower()+"_property_group_info"]), (row["i_d"],template)
                vitality(pid, 150)
                old_gold = wallet(pid,101)
            assert call(sock,"DungeonSweep",34,dungeon_id=120202).status==pb.StatusCode_DungeonUnlock
            gold = call(sock,"DungeonSweep",35,dungeon_id=120201)
            assert gold.status==pb.StatusCode_OK and qty(gold)=={1:300,101:1200}
            assert call(sock,"DungeonSweep",35,dungeon_id=120201)==gold
            with host.lock:
                assert wallet(pid,101)==old_gold+1200 and wallet(pid,10)==120 and load(pid,120201).finish_times==1
            pref = [{"int_value":120101}, {"int_value":2}]
            assert call(sock,"GenericGameA",36,generic_msg_id=2,params=pref).status==pb.StatusCode_OK
            pref[1]["int_value"]=4
            assert call(sock,"GenericGameB",37,generic_msg_id=2,params=pref).status==pb.StatusCode_BAD_REQ
            book = call(sock,"DungeonSweep",38,dungeon_id=120101)
            assert book.status==pb.StatusCode_OK and qty(book)=={1:600,191:2}
            with host.lock, db.conn:
                db.conn.execute("UPDATE players SET level=50 WHERE player_id=?",(pid,))
            armor = call(sock,"DungeonSweep",39,dungeon_id=220201)
            assert armor.status==pb.StatusCode_OK,armor.status
            gear = next(i.main_item.armor for i in armor.rewards if i.main_item.HasField("armor"))
            assert gear.instance_id and gear.level==_table("Dungeon","dungeon")[220201]["monster_lv"]
            with host.lock,db.conn:
                vitality(pid,0)
                assert db.get_item_detail(pid,instance_id=gear.instance_id)
            assert call(sock,"DungeonSweep",40,dungeon_id=120101).status==pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock:
                assert wallet(pid,10)==0 and load(pid,120101).finish_times==1
        host.stop()
        host.start()
        sock,same=connect()
        with sock:
            query = call(sock,"GenericGameB",48,generic_msg_id=2,params=[{"int_value":120101}])
            assert query.status==pb.StatusCode_OK and [p.int_value for p in query.params]==[120101,2]
            with host.lock:
                assert same==pid and db.get_item_detail(pid,instance_id=gear.instance_id)
    finally:
        host.stop()
    print("PASS 152 sweep plans, gold payout, EXP books, actual persistent equipment, vitality, replay, restart and tier selection.")


if __name__=="__main__":
    args=argparse.ArgumentParser()
    args.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.parse_args().resources)
