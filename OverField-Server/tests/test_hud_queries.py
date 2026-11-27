"""Native HUD reads observed during sustained private-realm Unity gameplay."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import pickle
import sqlite3
from tempfile import TemporaryDirectory
import time

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join_after_intro,login
from test_observed_initialization import call


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    users=[ident.create_profile(n) for n in ("界面甲","界面乙")]
    game=LocalGameService(ident,root,resources,port=0)
    def connect(index):
        sock,_,pid=login(game.port,ident.issue_ticket(*users[index]))
        join_after_intro(sock)
        return sock,pid
    game.start()
    try:
        a,pa=connect(0)
        b,pb_id=connect(1)
        with a,b:
            assert not call(a,"Friend",type=0).info
            assert not call(a,"SelfIntervalInit").is_start
            assert not call(a,"FriendIntervalInit").friend_infos
            assert not call(a,"SceneProcessList").scene_process_list
            first=call(a,"SupplyBoxInfo").next_reward_time
            assert call(a,"SupplyBoxInfo").next_reward_time==first
            assert call(a,"ShopInfo",shop_id=4294967295).status==pb.StatusCode_CFG_NOT_FOUND
            grid=call(a,"ShopInfo",shop_id=100).grids[0]
            assert (grid.id,grid.grid_id,grid.pool_id,grid.pool_index,grid.buy_times)==(100,1,150,1,0)
            assert call(a,"GenericGameB",generic_msg_id=6).params[0].int_value==1003
            assert call(a,"GenericGameB",generic_msg_id=2).status!=pb.StatusCode_OK
            assert call(a,"AbilityBadgeList",scene_id=9999).status==pb.StatusCode_OK
            assert call(a,"GamePlayReward",reward_id=0).status!=pb.StatusCode_OK
            assert call(a,"Tutorial",tutorial_id=9).status!=pb.StatusCode_OK
            assert call(a,"ChangeMusicalItem").status==pb.StatusCode_OK
            moon=json.loads((resources/"data/MoonStone.json").read_bytes())
            scene=moon["stone_area_data"]["datas"][0]["scene_i_d"]
            data={"interval_id":3,"finish_time":int(time.time()*1000)+60000,"create_time":int(time.time()*1000),
                  "member":[{"player_id":pb_id,"nick_name":"界面乙","head":41101,"join_time":1,"is_reward":False}]}
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                created=db.execute("SELECT create_time FROM players WHERE player_id=?",(pa,)).fetchone()[0]
                assert created+600 <= first <= created+1800
                db.execute("INSERT INTO friend(player_id,friend_id,friend_status,alias) VALUES(?,?,2,'test')",(pb_id,pa))
                db.execute("INSERT INTO player_kv VALUES(?,'interval','state',?)",(pa,pickle.dumps(data)))
                db.execute("INSERT INTO player_kv VALUES(?,'ach','exp:9999',?)",(pa,4))
                db.execute("INSERT INTO player_kv VALUES(?,'shop_buy','150:p',2)",(pa,))
                db.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,'badge_achieve','50001000')",(pa,))
                db.execute("INSERT OR REPLACE INTO ability_tree(player_id,scene_id,tree_blob) VALUES(?,?,?)",(pa,scene,pickle.dumps([1,2])))
                db.commit()
            friends=call(b,"Friend",type=2)
            assert [(v.info.player_id,v.alias) for v in friends.info]==[(pa,"test")]
            assert not call(b,"Friend",type=1).info
            fir=call(a,"SelfIntervalInit")
            assert fir.is_start and fir.interval_id==3 and fir.interval.player_id==pa
            intervals=call(b,"FriendIntervalInit")
            assert not intervals.friend_infos and [v.player_id for v in intervals.join_infos]==[pa]
            assert [(v.scene_id,v.process) for v in call(a,"SceneProcessList").scene_process_list]==[(9999,4)]
            assert not call(b,"SceneProcessList").scene_process_list
            badges=call(a,"AbilityBadgeList",scene_id=scene)
            assert list(badges.ability_badge_pages[0].active_box_ids)==[1,2]
            assert 50001000 in badges.ability_badge_reward_achieve_ids
            assert call(a,"ShopInfo",shop_id=100).grids[0].buy_times==2
            assert call(b,"ShopInfo",shop_id=100).grids[0].buy_times==0
        game.stop()
        game.start()
        a,_=connect(0)
        with a:
            assert call(a,"SupplyBoxInfo").next_reward_time==first
            assert call(a,"ShopInfo",shop_id=100).grids[0].buy_times==2
    finally:game.stop()
    print("PASS native HUD friend/interval/shop/scene/ability/supply/region reads, existing saves, fallback writes and restart.")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
