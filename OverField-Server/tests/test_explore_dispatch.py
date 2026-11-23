"""Exercise the national dispatch protocol against actual private TCP and persistent game state."""
import argparse
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from test_game_realm import login,join
from utils.pb_create import make_item

def call(peer,name,**values):
    request=getattr(pb,name+"Req")(**values)
    message_id=getattr(MsgId,name+"Req")
    peer.sendall(encode(message_id,request.SerializeToString(),message_id))
    for _ in range(200):
        header,body=receive(peer)
        if header.msg_id==getattr(MsgId,name+"Rsp"):
            assert header.packet_id==message_id
            return getattr(pb,name+"Rsp").FromString(body)
    raise AssertionError(name)

def run(resources):
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        identity=LocalIdentity(root/"identity.sqlite3")
        first=identity.create_profile("调查队长")
        second=identity.create_profile("另一调查员")
        def connect(game,profile):
            peer,_,player=login(game.port,identity.issue_ticket(*profile))
            join(peer)
            return peer,player
        def amount(pid,item):
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                row=db.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(pid,item)).fetchone()
            return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0
        def set_amount(pid,item,count):
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                value=make_item(item,count,pid)
                db.execute("INSERT OR REPLACE INTO items(player_id,item_id,item_detail_blob) VALUES(?,?,?)",(pid,item,value.SerializeToString()))
                db.commit()
        game=LocalGameService(identity,root,resources,port=0);game.start()
        try:
            a,pid=connect(game,first)
            b,bid=connect(game,second)
            with a,b:
                initial=call(a,"ExploreInit")
                assert {x.explore_id for x in initial.explore}=={10101,10102,10201,10202,10301}
                assert not initial.activity_explore and not any(x.collect_reward or x.collect_item_id or x.times for x in initial.explore)
                assert call(a,"Explore",explore_id=901,is_activity=True).status==pb.StatusCode_MODULE_CLOSE
                assert call(a,"Explore",explore_id=999).status==pb.StatusCode_CFG_NOT_FOUND
                assert call(a,"ExploreCollectReward",explore_id=10101).status==pb.StatusCode_EXPLORE_COLLECT_NOT_FINISH
                assert call(a,"ExploreCancel",explore_id=10101).status==pb.StatusCode_EXPLORE_NOT_EXISTS
                assert amount(pid,100001502)>=1 and amount(pid,100001503)>=1
                # Both permits are permanent. Permission is checked, and failed starts do not consume a slot.
                set_amount(pid,100001502,0)
                assert call(a,"Explore",explore_id=10201).status==pb.StatusCode_ITEM_NOT_ENOUGH
                set_amount(pid,100001502,3)
                assert call(a,"Explore",explore_id=10201).status==pb.StatusCode_OK and amount(pid,100001502)==3
                assert call(a,"ExploreCancel",explore_id=10201).status==pb.StatusCode_OK
                # No speeding up before a job exists; insufficient material leaves both job and inventory intact.
                assert call(a,"ExploreQuickFinish",explore_id=10101).status==pb.StatusCode_EXPLORE_NOT_EXISTS
                set_amount(pid,102,0)
                before={i:amount(pid,i) for i in (101,109)}
                result=call(a,"Explore",explore_id=10101)
                start=result.explore.start_time
                assert result.status==pb.StatusCode_OK and abs(start-time.time_ns()//1_000_000)<8000
                assert call(a,"Explore",explore_id=10101).status==pb.StatusCode_EXPLORE_IN_PROGRESS
                assert call(a,"Explore",explore_id=10102).status==pb.StatusCode_EXPLORE_NUM_LIMIT
                assert call(a,"ExploreReward",explore_id=10101).status==pb.StatusCode_EXPLORE_NOT_FINISH
                assert call(a,"ExploreQuickFinish",explore_id=10101).status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert call(b,"Explore",explore_id=10102).status==pb.StatusCode_OK
                set_amount(pid,102,2)
                assert call(a,"ExploreQuickFinish",explore_id=10101).explore.is_quick_finish
                assert call(a,"ExploreQuickFinish",explore_id=10101).status==pb.StatusCode_REPEAT_GET_REWARD and amount(pid,102)==1
        finally:game.stop()
        game=LocalGameService(identity,root,resources,port=0);game.start()
        try:
            a,again=connect(game,first);b,other=connect(game,second)
            with a,b:
                assert again==pid and other==bid and pid!=bid
                pending=next(x for x in call(a,"ExploreInit").explore if x.explore_id==10101)
                assert pending.is_quick_finish and pending.start_time==start
                result=call(a,"ExploreReward",explore_id=10101)
                assert result.status==pb.StatusCode_OK and result.explore.times==1 and result.explore.start_time==0
                assert set(result.explore.collect_item_id)=={101,109} and not result.explore.collect_reward
                assert result.interval_id in (101,102,103)
                assert {v.main_item.item_id:v.main_item.base_item.num for v in result.reward_item}=={101:10,109:10}
                assert {i:amount(pid,i)-n for i,n in before.items()}=={101:10,109:10}
                assert call(a,"ExploreReward",explore_id=10101).status==pb.StatusCode_EXPLORE_NOT_EXISTS
                claimed=call(a,"ExploreCollectReward",explore_id=10101)
                assert claimed.status==pb.StatusCode_OK and claimed.explore.collect_reward
                assert {i:amount(pid,i)-n for i,n in before.items()}=={101:15,109:15}
                assert call(a,"ExploreCollectReward",explore_id=10101).status==pb.StatusCode_REPEAT_GET_REWARD
                assert call(b,"ExploreCollectReward",explore_id=10101).status==pb.StatusCode_EXPLORE_COLLECT_NOT_FINISH
                # Persist a start older than its 1,800-second national timer. Normal settlement must not charge a fast pass.
                with closing(sqlite3.connect(root/"game.sqlite")) as db:
                    db.execute("UPDATE private_explorations SET started_ms=? WHERE player_id=? AND area=10102",
                               (time.time_ns()//1_000_000-1801000,bid));db.commit()
                before_fast=amount(bid,102)
                assert call(b,"ExploreQuickFinish",explore_id=10102).status==pb.StatusCode_REPEAT_GET_REWARD
                normal=call(b,"ExploreReward",explore_id=10102)
                assert normal.status==pb.StatusCode_OK and normal.explore.times==1 and amount(bid,102)==before_fast
                assert set(normal.explore.collect_item_id)=={2070,10,2020,101}
                again=call(a,"ExploreInit")
                assert next(v for v in again.explore if v.explore_id==10101).collect_reward
        finally:game.stop()
    print("PASS genuine native dispatch lifecycle, persistent jobs, catalog, permits, star cost, elapsed time, independent settlement and interval discovery.")

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--resources",type=Path,required=True)
    run(p.parse_args().resources)
