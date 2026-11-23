"""Drive the native 1833–1844 rift lifecycle through two authenticated TCP game connections."""
import argparse
import json
from contextlib import closing
from pathlib import Path
import pickle
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join,login
from test_observed_initialization import call


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    users=[identity.create_profile(name) for name in ("队长","好友","其他玩家")]
    game=LocalGameService(identity,root,resources,port=0)
    database=root/"game.sqlite"
    clock=[1_700_000_000_000]

    def connect(index):
        sock,_,player=login(game.port,identity.issue_ticket(*users[index]))
        profile,_=join(sock)
        return sock,player,profile

    def inventory(player,item):
        with closing(sqlite3.connect(database)) as connection:
            row=connection.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(player,item)).fetchone()
            return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0

    def loot(result):
        assert result.status==pb.StatusCode_OK,result.status
        return {piece.main_item.item_id:piece.main_item.base_item.num for piece in result.rewards}

    def fresh_offer(player,area):
        from utils import db
        from private_intervals import create_offer
        with game.lock,db.conn:
            return create_offer(SimpleNamespace(player_id=player),area)

    game.start()
    try:
        with patch("private_intervals.now",side_effect=lambda:clock[0]):
            a,owner,profile=connect(0)
            b,friend,_=connect(1)
            c,stranger,_=connect(2)
            with a,b,c:
                assert 100000023 in profile.unlock_functions
                entrance=call(a,"SelfIntervalInit")
                assert not entrance.is_start and entrance.interval_id==101
                assert entrance.end_time==clock[0]+172800000
                assert call(a,"SelfIntervalInit")==entrance
                assert not fresh_offer(owner,10101)
                assert call(c,"SelfIntervalInit").interval_id==101
                assert not call(b,"FriendIntervalInit").friend_infos
                stamina=inventory(owner,10)
                first=call(a,"IntervalStart")
                assert first.status==pb.StatusCode_OK and first.explore_id==101
                assert first.finish_time==clock[0]+14400000
                assert call(a,"IntervalStart")==first
                assert inventory(owner,10)==stamina-20
                state=call(a,"SelfIntervalInit")
                assert state.is_start and state.interval.player_id==owner and state.interval.create_time==clock[0]
                assert len(state.interval.member)==1
                captain=state.interval.member[0]
                assert captain.player_id==owner and captain.join_time==state.interval.create_time
                assert captain.nick_name==profile.player_name and captain.head==profile.head and not captain.is_reward
                token=str(state.interval.create_time)
                fields=dict(friend_id=owner,interval_param=token)
                assert call(a,"IntervalReward",**fields).status==pb.StatusCode_INTERVAL_NOT_FINISH
                assert call(c,"IntervalReward",**fields).status!=pb.StatusCode_OK
                assert call(b,"IntervalJoin",**fields).status==pb.StatusCode_FRIEND_NOT_EXIST
                assert call(a,"IntervalJoin",**fields).status==pb.StatusCode_FRIEND_NOT_EXIST
                with closing(sqlite3.connect(database)) as dbc,dbc:
                    dbc.executemany("INSERT INTO friend(player_id,friend_id,friend_status,alias) VALUES(?,?,2,'')",
                                    [(owner,friend),(friend,owner)])
                before=inventory(friend,10)
                assert call(b,"FriendIntervalInit").friend_infos[0].create_time==clock[0]
                assert call(b,"IntervalJoin",friend_id=owner,interval_param="1234").status==pb.StatusCode_INTERVAL_NOT_EXISTS
                assert inventory(friend,10)==before
                joined=call(b,"IntervalJoin",**fields)
                assert joined.status==pb.StatusCode_OK,joined.status
                finish=first.finish_time-600000
                assert joined.finish_time==finish and joined.friend_id==str(owner)
                assert call(b,"IntervalJoin",**fields)==joined
                assert inventory(friend,10)==before-10
                assert [m.player_id for m in call(a,"SelfIntervalInit").interval.member]==[owner,friend]
                assert call(b,"FriendIntervalInit").join_infos[0].create_time==int(token)
                # Full-price acceleration must fail while one minute can still be bought.
                from utils import db
                with game.lock,db.conn:
                    stars=pb.ItemDetail.FromString(db.get_item_detail(owner,102))
                    stars.main_item.base_item.num=200
                    db.set_item_detail(owner,stars.SerializeToString(),102)
                premium=inventory(owner,102)
                assert call(a,"IntervalQuick").status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert inventory(owner,102)==premium
                clock[0]=finish-61000
                quickly=call(a,"IntervalQuick")
                assert quickly.status==pb.StatusCode_OK and quickly.finish_time==clock[0]
                assert inventory(owner,102)==premium-20
                assert call(a,"IntervalQuick").status==pb.StatusCode_INTERVAL_FINISH
                assert inventory(owner,102)==premium-20
                assert not call(b,"FriendIntervalInit").friend_infos
                assert call(b,"FriendIntervalInit").join_infos[0].create_time==int(token)
                assert not call(c,"FriendIntervalInit").join_infos
                assert call(c,"IntervalReward",**fields).status!=pb.StatusCode_OK
                originals={i:inventory(owner,i) for i in [101,109]}
                assert loot(call(a,"IntervalReward",**fields))=={101:2,109:2}
                assert {i:inventory(owner,i)-current for i,current in originals.items()}=={101:2,109:2}
                assert call(a,"IntervalReward",**fields).status==pb.StatusCode_REPEAT_GET_REWARD
                assert not call(a,"SelfIntervalInit").interval_id
                assert call(a,"IntervalStart").status==pb.StatusCode_INTERVAL_NOT_EXISTS
                assert fresh_offer(owner,10101) in (101,102,103)
                assert call(a,"IntervalStart").status==pb.StatusCode_OK
                next_state=call(a,"SelfIntervalInit")
                assert next_state.interval.create_time!=int(token)
                assert call(a,"IntervalReward",**fields).status==pb.StatusCode_REPEAT_GET_REWARD
                assert call(b,"IntervalReward",friend_id=owner,interval_param=str(next_state.interval.create_time)).status!=pb.StatusCode_OK
            game.stop()
            with closing(sqlite3.connect(database)) as dbc,dbc:
                dbc.execute("UPDATE players SET unlock_functions=? WHERE player_id=?",(pickle.dumps([100000000]),owner))
            game.start()
            a,again,profile=connect(0)
            b,_,_=connect(1)
            c,_,_=connect(2)
            with a,b,c:
                rows=json.loads((resources/"data/PlayerUnlock.json").read_bytes())["player_unlock"]["datas"]
                configured={row["i_d"] for row in rows}
                assert again==owner and 100000023 in configured
                assert set(profile.unlock_functions)==configured|{100000000}
                assert call(a,"SelfIntervalInit").interval.create_time==next_state.interval.create_time
                former=call(b,"FriendIntervalInit").join_infos[0]
                assert former.create_time==int(token)
                assert [(m.player_id,m.is_reward) for m in former.member]==[(owner,True),(friend,False)]
                before={i:inventory(friend,i) for i in [101,109]}
                assert loot(call(b,"IntervalReward",**fields))=={101:1,109:1}
                assert {i:inventory(friend,i)-v for i,v in before.items()}=={101:1,109:1}
                assert call(b,"IntervalReward",**fields).status==pb.StatusCode_REPEAT_GET_REWARD
                assert not call(b,"FriendIntervalInit").join_infos
                clock[0]=entrance.end_time+1
                assert not call(c,"SelfIntervalInit").interval_id
                assert call(c,"IntervalStart").status==pb.StatusCode_INTERVAL_NOT_EXISTS
                assert fresh_offer(stranger,10101) in (101,102,103)
                assert call(c,"SelfIntervalInit").interval_id
                assert loot(call(a,"IntervalReward",friend_id=owner,interval_param=str(next_state.interval.create_time)))=={101:2,109:2}
    finally:
        game.stop()
    print("PASS native PC interval discovery/expiry, captain/peer stamina, start/join/fast finish, persistent separate reward claims, genuine new discoveries and old-save unlock.")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),ap.parse_args().resources)
