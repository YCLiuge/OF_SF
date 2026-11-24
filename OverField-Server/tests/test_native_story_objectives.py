"""Confirmed story condition sources: saved ability, successful recipe, per-map ten-thousandths."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest, make_item


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    realm=LocalGameService(identity,root,resources,port=0)
    realm.start()
    def connect(profile):
        sock,_,pid=login(realm.port,identity.issue_ticket(*profile))
        main,_=join(sock)
        return sock,pid,{q.quest_id:q for q in main.quest_detail.quests}
    def seed(pid,*steps):
        for step in steps:
            db.set_quest(pid,step,make_Quest(pid,step).SerializeToString())
    def stock(pid,item,quantity):
        db.set_item_detail(pid,make_item(item,quantity,pid).SerializeToString(),item)
    def current(pid,qid):
        with realm.lock:
            raw=db.get_quest(pid,qid)
            return pb.Quest.FromString(raw) if raw else None
    def act(sock,cmd,**kw):
        return call(sock,cmd,48,**kw)
    try:
        a=identity.create_profile("已有章节条件")
        b=identity.create_profile("独立章节条件")
        sock,aid,_=connect(a)
        with sock:
            assert act(sock,"ChangeSceneChannel",scene_id=1).status==pb.StatusCode_OK
            with realm.lock,db.conn:
                seed(aid,100032,100086,100093)
                stock(aid,101000000,1)
                db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'ach','exp:1','45')",(aid,))
                stock(aid,11,30)
                stock(aid,5011004,0)
        sock,bid,_=connect(b)
        with sock,realm.lock,db.conn:
            seed(bid,100032,100086,100093)
        realm.stop()
        realm.start()
        sock,_,qs=connect(a)
        with sock:
            assert qs[100032].status==pb.QuestStatus_Finish and qs[100033].status==pb.QuestStatus_InProgress
            assert qs[100093].conditions[0].progress==45*10000//456 and 100094 not in qs
            assert act(sock,"Collecting",item_id=10801001).status==pb.StatusCode_OK
            assert current(aid,100093).conditions[0].progress==1000 and current(aid,100094)
            with realm.lock:
                energy=pb.ItemDetail.FromString(db.get_item_detail(aid,11)).main_item.base_item.num
            assert act(sock,"CookingFood",food_id=42).status==pb.StatusCode_CFG_NOT_FOUND
            assert act(sock,"CookingFood",food_id=5012001).status==pb.StatusCode_ITEM_NOT_ENOUGH
            with realm.lock,db.conn:
                assert pb.ItemDetail.FromString(db.get_item_detail(aid,11)).main_item.base_item.num==energy
                stock(aid,5011004,4)
            with patch("handlers.CookingFood.random.random",return_value=1):
                fail=act(sock,"CookingFood",food_id=5012001)
            assert fail.status==pb.StatusCode_OK and not fail.life_common_result.is_success
            assert current(aid,100086).conditions[0].progress==0
            with patch("handlers.CookingFood.random.random",return_value=0):
                success=act(sock,"CookingFood",food_id=5012001)
            assert success.status==pb.StatusCode_OK and success.life_common_result.is_success
            assert [i.main_item.item_id for i in success.life_common_result.result_items]==[5012001]
            assert current(aid,100086).status==pb.QuestStatus_Finish and current(aid,100087)
            with realm.lock:
                assert pb.ItemDetail.FromString(db.get_item_detail(aid,5011004)).main_item.base_item.num==0
                assert pb.ItemDetail.FromString(db.get_item_detail(aid,11)).main_item.base_item.num==energy-6
                assert int(db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='life_book' AND k='1:5012001'",(aid,)).fetchone()[0])==1
        sock,_,qs=connect(b)
        with sock:
            assert all(qs[q].status==pb.QuestStatus_InProgress and qs[q].conditions[0].progress==0 for q in (100032,100086,100093))
        realm.stop()
        realm.start()
        sock,_,qs=connect(a)
        with sock:
            assert all(qs[q].status==pb.QuestStatus_Finish for q in (100032,100086,100093))
            assert all(qs[q].status==pb.QuestStatus_InProgress for q in (100033,100087,100094))
    finally:
        realm.stop()
    print("PASS real cook costs and outcomes, double-jump ownership, map threshold and per-role reconnect.")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
