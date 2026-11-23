"""Run PC ShopInfo -> ShopBuy -> inventory across two authenticated roles and a restarted realm."""
import argparse
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from test_game_realm import join,login
from test_observed_initialization import call


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    accounts=[identity.create_profile(n) for n in ("集市一","集市二")]
    game=LocalGameService(identity,root,resources,port=0)
    stamp=[datetime(2026,11,24,13,tzinfo=timezone(timedelta(hours=8)))]
    def connect(i):
        sock,_,pid=login(game.port,identity.issue_ticket(*accounts[i]))
        join(sock)
        return sock,pid
    def inventory(pid,template):
        with closing(sqlite3.connect(root/"game.sqlite")) as dbc:
            line=dbc.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(pid,template)).fetchone()
            return pb.ItemDetail.FromString(line[0]).main_item.base_item.num if line else 0
    def fixture_coins(pid,item,amount):
        from utils import db
        from utils.pb_create import make_item
        with game.lock,db.conn:
            db.set_item_detail(pid,make_item(item,amount,pid).SerializeToString(),item)
    def purchase(s,packet,shop,grid,quantity,item=0):
        req=pb.ShopBuyReq(shop_id=shop,grid_id=grid,buy_times=quantity,item_id=item)
        s.sendall(encode(MsgId.ShopBuyReq,req.SerializeToString(),packet))
        for _ in range(100):
            head,body=receive(s)
            if head.packet_id==packet and head.msg_id==MsgId.ShopBuyRsp:
                return pb.ShopBuyRsp.FromString(body)
        raise AssertionError("missing shop receipt")
    def deltas(response):
        return {d.main_item.item_id:d.main_item.base_item.num for d in response.items}
    game.start()
    try:
        with patch("private_shop.now",side_effect=lambda:stamp[0]):
            a,owner=connect(0)
            b,other=connect(1)
            with a,b:
                for player in (owner,other):
                    fixture_coins(player,101,500)
                fixture_coins(owner,102,200)
                fixture_coins(owner,108,0)
                board=call(a,"ShopInfo",shop_id=11000)
                assert board.grids[0].pool_id==11001 and board.grids[0].buy_times==0
                coins,material=inventory(owner,101),inventory(owner,5011001)
                sale=purchase(a,60,11000,1,3,5011001)
                assert sale.status==pb.StatusCode_OK and sale.grids.buy_times==3
                assert deltas(sale)=={5011001:30}
                assert purchase(a,60,11000,1,3,5011001)==sale
                assert inventory(owner,101)==coins-30 and inventory(owner,5011001)==material+30
                for packet,grid,qty,item in ((61,1,0,0),(62,1,1001,0),(63,1,1,5011002),(64,65535,1,0)):
                    assert purchase(a,packet,11000,grid,qty,item).status!=pb.StatusCode_OK
                assert inventory(owner,101)==coins-30 and call(a,"ShopInfo",shop_id=11000).grids[0].buy_times==3
                assert purchase(a,65,100,1,1).status==pb.StatusCode_MODULE_CLOSE
                assert purchase(a,66,11000,1,100).status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert call(b,"ShopInfo",shop_id=11000).grids[0].buy_times==0 and inventory(other,101)==coins
                assert purchase(a,67,11000,1,1).grids.buy_times==4
                gift=purchase(a,68,400,1,1)
                assert gift.status==pb.StatusCode_OK and deltas(gift)=={190:2,101:1000,1501:2},gift
                assert purchase(a,69,400,1,1).status==pb.StatusCode_SHOP_GRID_BUY_NUM_LIMIT
                assert purchase(b,70,400,1,1).status==pb.StatusCode_OK
                gear=purchase(a,71,15000,1,1)
                assert gear.status==pb.StatusCode_OK and gear.items[0].main_item.weapon.instance_id>0,gear
                assert purchase(a,72,15000,1,1).status==pb.StatusCode_SHOP_GRID_BUY_NUM_LIMIT
                remain=inventory(owner,102)
                assert purchase(a,73,500,1,1).status==pb.StatusCode_OK
                assert inventory(owner,102)==remain-150
                assert purchase(a,74,500,1,1).status==pb.StatusCode_ITEM_NOT_ENOUGH
                # Original event window and increasing-price table: base 2, then 4, then 6.
                assert purchase(a,75,19000,1,1).status==pb.StatusCode_MODULE_CLOSE
                stamp[0]=datetime(2025,4,10,12,tzinfo=stamp[0].tzinfo)
                fixture_coins(owner,6019,12)
                for pid,remaining in [(80,10),(81,6),(82,0)]:
                    assert purchase(a,pid,19000,1,1).status==pb.StatusCode_OK
                    assert inventory(owner,6019)==remaining
                assert purchase(a,83,19000,1,1).status==pb.StatusCode_SHOP_GRID_BUY_NUM_LIMIT
                # In-window 70% national costume listing costs 180 * .7 = 126 private stones.
                stamp[0]=datetime(2025,6,12,12,tzinfo=stamp[0].tzinfo)
                fixture_coins(owner,108,200)
                from utils import db
                with game.lock,db.conn:
                    db.conn.execute("DELETE FROM items WHERE player_id=? AND item_id=?",(owner,4640181))
                discounted=purchase(a,84,900,14,1)
                assert discounted.status==pb.StatusCode_OK and inventory(owner,108)==74,discounted
                assert discounted.items[0].main_item.WhichOneof("item")=="outfit"
                # The stock client confirms the shortfall before issuing its ordinary purchase request.
                fixture_coins(owner,102,100)
                assert purchase(a,85,500,1,1).status==pb.StatusCode_OK
                assert inventory(owner,102)==0 and inventory(owner,108)==24
            game.stop()
            game.start()
            stamp[0]=datetime(2026,11,24,14,tzinfo=stamp[0].tzinfo)
            a,_=connect(0)
            b,_=connect(1)
            with a,b:
                assert call(a,"ShopInfo",shop_id=11000).grids[0].buy_times==4
                assert call(b,"ShopInfo",shop_id=11000).grids[0].buy_times==0
                assert purchase(a,90,400,1,1).status==pb.StatusCode_SHOP_GRID_BUY_NUM_LIMIT
                stamp[0]+=timedelta(days=1)
                assert purchase(a,91,400,1,1).status==pb.StatusCode_OK
    finally:
        game.stop()
    print("PASS native NPC and virtual-currency shops: configured amounts/discounts/price ladders, equipment, free daily box, atomic payments, retry, role isolation and reset/restart.")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as path:
        run(Path(path),p.parse_args().resources)
