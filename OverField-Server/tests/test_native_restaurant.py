"""Drive all four 1.1.12 restaurant calls over native TCP and restore a real character snapshot."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from migrate import backup, restore
import private_restaurant as restaurant
from test_game_dungeon import call
from test_game_realm import login, join
from utils import db


class Deterministic:
    @staticmethod
    def choice(values):
        return values[0]

    @staticmethod
    def randint(low,high):
        return high


def run(base,resources):
    root=base/"realm";root.mkdir()
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("料理甲","料理乙")]
    host=LocalGameService(identity,root,resources,port=0)
    def connect(stack,index):
        sock,_,_=login(host.port,identity.issue_ticket(*profiles[index]))
        stack.enter_context(sock);join(sock)
        return sock
    def action(sock,kind,activity=12000,**args):
        return call(sock,"RestaurantGame"+kind,activity_id=activity,**args)
    def coin(player,item=1601):
        with host.lock:
            raw=db.get_item_detail(player,item)
            return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
    def buy(sock):
        result=action(sock,"Operate",type=pb.RestaurantGameOperateType_Buy,buy_pool_id=1001)
        assert result.status==pb.StatusCode_OK
        return result.grid[0]
    def combine(sock,first,second):
        return action(sock,"Operate",type=pb.RestaurantGameOperateType_Mix,grid=[first,second])
    def rewards(info):
        return {q.quest_id:q.status for q in info.question_info}
    host.start()
    try:
        with ExitStack() as live, patch.object(restaurant,"random",Deterministic), patch.object(restaurant,"calendar_day",return_value="2026-11-24"):
            a=connect(live,0);b=connect(live,1)
            pa,pb_id=[db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(p[0],)).fetchone()[0] for p in profiles]
            # Start these synthetic characters with empty cafe wallets to exercise insufficient funds.
            with host.lock, db.conn:
                for player in (pa, pb_id):
                    db.conn.execute("DELETE FROM items WHERE player_id=? AND item_id IN (1600,1601)", (player,))
            assert action(a,"Info",activity=888).status==pb.StatusCode_CFG_NOT_FOUND
            for theme in range(12000,12004):
                snapshot=action(a,"Info",activity=theme)
                assert snapshot.status==pb.StatusCode_OK and snapshot.activity_id==theme
                assert len(snapshot.grid_list)==24 and snapshot.dungeon_info.dungeon_id==1001
                assert len(snapshot.dungeon_info.customer_list)==4 and rewards(snapshot.dungeon_info)[101]==pb.QuestStatus_Completed
            assert action(a,"QuestReward",quest_id=1).status==pb.StatusCode_FAIL
            assert action(a,"Operate",type=1,buy_pool_id=1003).status==pb.StatusCode_CFG_NOT_FOUND
            assert action(a,"Operate",type=1,buy_pool_id=1001).status==pb.StatusCode_ITEM_NOT_ENOUGH
            with ThreadPoolExecutor(max_workers=2) as pool:
                received=list(pool.map(lambda sock:action(sock,"QuestReward",quest_id=101),(a,b)))
            assert all(r.status==pb.StatusCode_OK and r.item_details[0].main_item.base_item.num==600 for r in received)
            assert action(a,"QuestReward",quest_id=101).status==pb.StatusCode_REPEAT_GET_REWARD
            first=buy(a)
            assert combine(a,first.index,first.index).status==pb.StatusCode_BAD_REQ
            assert action(a,"Eat",grid=first.index,customer_index=1).status==pb.StatusCode_MINIGAME_RESTAURANT_FOOD_ERR
            assert action(b,"Operate",type=3,grid=[first.index]).status==pb.StatusCode_MINIGAME_RESTAURANT_GRID_EMPTY
            assert action(a,"Operate",type=3,grid=[first.index,999]).status!=pb.StatusCode_OK
            assert coin(pa)==595 and coin(pb_id)==600
            recycle=action(a,"Operate",type=3,grid=[first.index])
            assert recycle.status==pb.StatusCode_OK and recycle.item_details[0].main_item.base_item.num==7 and coin(pa)==602
            # Complete every configured serving in stage 1001 using only native buy, mix and eat calls.
            for customer in range(1,5):
                for _ in range(9):
                    first=buy(a);second=buy(a)
                    mixed=combine(a,first.index,second.index)
                    assert mixed.status==pb.StatusCode_OK and mixed.grid[-1].food==2
                    serve=action(a,"Eat",grid=second.index,customer_index=customer)
                    assert serve.status==pb.StatusCode_OK
            assert serve.dungeon_info.dungeon_id==1002 and rewards(serve.dungeon_info)[1]==pb.QuestStatus_Completed
            assert action(a,"QuestReward",quest_id=1).status==pb.StatusCode_OK
            assert coin(pa,1600)==1 and action(a,"QuestReward",quest_id=1).status==pb.StatusCode_REPEAT_GET_REWARD
            before=coin(pa)
            bought=buy(a)
            assert coin(pa)==before-5
            assert action(b,"Info").dungeon_info.dungeon_id==1001
            assert action(a,"Info",activity=12003).dungeon_info.dungeon_id==1001
    finally:host.stop()
    archive=backup(root,base/"restaurant.zip")
    restored=restore(archive,base/"restored")
    identity=LocalIdentity(restored/"identity.sqlite3");host=LocalGameService(identity,restored,resources,port=0)
    host.start()
    try:
        with ExitStack() as live, patch.object(restaurant,"random",Deterministic):
            a=connect(live,0)
            with patch.object(restaurant,"calendar_day",return_value="2026-11-24"):
                snapshot=action(a,"Info")
                assert snapshot.dungeon_info.dungeon_id==1002 and rewards(snapshot.dungeon_info)[1]==pb.QuestStatus_Finish
                assert next(g for g in snapshot.grid_list if g.index==bought.index).food==1 and coin(pa)==before-5
                # Seed only the final serving of the recurring endgame stage. The first stage above is unshortened.
                cfg=restaurant.activity_config(12000)
                with host.lock,db.conn:
                    state,_=restaurant.load(pa,12000,cfg,"2026-11-24")
                    state.update(stage=1000,completed=list(range(1001,1031)),customers=[[5,1]],board={"1":5})
                    restaurant.write(pa,12000,state)
                finish=action(a,"Eat",grid=1,customer_index=1)
                assert finish.status==pb.StatusCode_OK and finish.dungeon_info.customer_list[0].status
                assert action(a,"QuestReward",quest_id=31).status==pb.StatusCode_OK
                assert action(a,"QuestReward",quest_id=31).status==pb.StatusCode_REPEAT_GET_REWARD
            with patch.object(restaurant,"calendar_day",return_value="2026-11-25"):
                tomorrow=action(a,"Info")
                assert len(tomorrow.dungeon_info.customer_list)==8 and not any(c.status for c in tomorrow.dungeon_info.customer_list)
                assert rewards(tomorrow.dungeon_info)[31]==pb.QuestStatus_InProgress
                assert action(a,"QuestReward",quest_id=101).status==pb.StatusCode_OK
                assert action(a,"QuestReward",quest_id=1).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:host.stop()
    print("PASS four native restaurant themes, two isolated players, 36 actual order servings, mix/recycle, rewards, migration and 05:00 recurring reset.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
