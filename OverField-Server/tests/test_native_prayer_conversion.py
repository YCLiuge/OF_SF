"""National HUD prayer-stone click, awarded fragments, legacy balances, native notices and restart."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from network.msg_id import MsgId
import private_arcade_wallet as wallet
from private_shop import CN
from test_game_realm import login,join_after_intro
from utils import db
from utils.pb_create import make_item


def exchange(sock, packet, bulk):
    req=pb.DailyTaskExchangeReq(exchange_all=bulk)
    sock.sendall(encode(MsgId.DailyTaskExchangeReq,req.SerializeToString(),packet))
    updates=[]
    bag={}
    for _ in range(256):
        head,body=receive(sock)
        if head.msg_id==MsgId.DailyTaskUpdateNotice:
            updates.append(pb.DailyTaskUpdateNotice.FromString(body))
        elif head.msg_id==MsgId.PackNotice:
            items=pb.PackNotice.FromString(body).items
            bag.update({v.main_item.item_id:v.main_item.base_item.num for v in items if v.main_item.item_id in (124,125)})
        elif head.msg_id==MsgId.DailyTaskExchangeRsp and head.packet_id==packet:
            return pb.DailyTaskExchangeRsp.FromString(body),updates,bag
    raise AssertionError("no native prayer exchange response")


def run(root,resources):
    current=datetime(2026,11,24,10,tzinfo=CN)
    ident=LocalIdentity(root/"identity.sqlite3")
    slot=ident.create_profile("月亮测试")
    game=LocalGameService(ident,root,resources,port=0)
    def connect():
        socket,_,pid=login(game.port,ident.issue_ticket(*slot))
        main,_=join_after_intro(socket)
        return socket,pid,main.daily_task
    def seed(pid,done,pending,fragments,stones):
        wallet.write_value(pid,"exchange:"+wallet._day(current),done)
        wallet.write_value(pid,"prayer_ready",pending)
        for iid,amount in ((124,fragments),(125,stones)):
            db.set_item_detail(pid,make_item(iid,amount,pid).SerializeToString(),iid)
    def positive(cell, n, counts):
        rsp,updates,bag=cell
        assert rsp.status==pb.StatusCode_OK and rsp.rewards[0].main_item.base_item.num==n
        assert (rsp.today_converted,rsp.exchange_times_left)==counts
        assert len(updates)==1 and (updates[0].today_converted,updates[0].exchange_times_left)==counts
        return bag

    game.start()
    try:
        with patch.object(wallet,"now",return_value=current):
            sock,pid,_=connect()
            with sock:
                with game.lock,db.conn:
                    seed(pid,2,2,10,10219)
                assert positive(exchange(sock,101,False),1,(2,1))[125]==10220
                assert positive(exchange(sock,102,True),1,(2,0))[125]==10221
                assert wallet.wallet(pid,124)==10
                assert exchange(sock,103,True)[0].status==pb.StatusCode_DAILY_WISH_TIMES_NOT_ENOUGH
                # A normal inventory grant crosses the next 60-fragment threshold.
                with game.lock:
                    with db.conn:
                        import private_shop
                        session=game.active[slot[0]]
                        pack=pb.PackNotice(status=pb.StatusCode_OK)
                        private_shop.award(pid,{124:50},pb.ShopBuyRsp(),pack)
                        session.send(MsgId.PackNotice,pack,0)
                    game._publish_pending()
                while True:
                    head,body=receive(sock)
                    if head.msg_id==MsgId.DailyTaskUpdateNotice:
                        formed=pb.DailyTaskUpdateNotice.FromString(body)
                        assert (formed.today_converted,formed.exchange_times_left)==(3,1)
                        break
                while True:
                    head,body=receive(sock)
                    if head.msg_id==MsgId.PackNotice:
                        held=pb.PackNotice.FromString(body)
                        assert [(i.main_item.item_id,i.main_item.base_item.num) for i in held.items]==[(124,60)]
                        break
                assert positive(exchange(sock,113,True),1,(3,0))[125]==10222
                with game.lock,db.conn:
                    seed(pid,21,0,991840,1000021)
            # A new native session must preview the historical balance without spending it.
            sock,same,main=connect()
            with sock:
                assert same==pid and (main.today_converted,main.exchange_times_left)==(219,198)
                assert wallet.wallet(pid,124)==991840 and wallet.wallet(pid,125)==1000021
                bag=positive(exchange(sock,104,True),198,(219,0))
                assert bag=={124:1840,125:1000219}
                duplicated,notices,bag=exchange(sock,104,True)
                assert duplicated.rewards[0].main_item.base_item.num==198 and not notices and not bag
                assert exchange(sock,105,True)[0].status==pb.StatusCode_DAILY_WISH_TIMES_NOT_ENOUGH
            game.stop()
            game.start()
            sock,same,main=connect()
            with sock:
                assert same==pid and (main.today_converted,main.exchange_times_left)==(219,0)
                assert wallet.wallet(pid,124)==1840 and wallet.wallet(pid,125)==1000219
                for packet,amount in ((106,0),(107,4999),(108,5000)):
                    with game.lock,db.conn:
                        db.set_item_detail(pid,make_item(124,amount,pid).SerializeToString(),124)
                    result=exchange(sock,packet,True)
                    if amount<5000:
                        assert result[0].status==pb.StatusCode_DAILY_WISH_TIMES_NOT_ENOUGH
                        assert wallet.wallet(pid,124)==amount
                    else:
                        assert positive(result,1,(220,0))=={124:0,125:1000220}
                with game.lock,db.conn:
                    seed(pid,220,2,0,1000220)
        game.stop()
        game.start()
        with patch.object(wallet,"now",return_value=current+timedelta(days=1)):
            sock,_,main=connect()
            with sock:
                assert (main.today_converted,main.exchange_times_left)==(0,2)
                assert positive(exchange(sock,109,True),2,(0,0))[125]==1000222
    finally:
        game.stop()
    print("PASS original 2/2/10 pending balance; 21 redemptions and 991840 fragments; 2601/2603, duplicate click, empty/exact/short, restart and prior-day claims.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
