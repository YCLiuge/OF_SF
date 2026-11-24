"""Exercise daily conversion, native coin purchases and the three-hour recovery clock."""
import argparse
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
import private_arcade_wallet as wallet
from private_shop import CN
from test_game_realm import login,join_after_intro
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    profile=ident.create_profile("街机测试")
    host=LocalGameService(ident,root,resources,port=0)
    def connect():
        sock,_,pid=login(host.port,ident.issue_ticket(*profile))
        join_after_intro(sock)
        return sock,pid
    def give(pid,item,qty):
        db.set_item_detail(pid,make_item(item,qty,pid).SerializeToString(),item)
    date=datetime(2026,10,11,4,59,tzinfo=CN)
    host.start()
    try:
        sock,pid=connect()
        with sock,patch.object(wallet,"now",return_value=date):
            with host.lock,db.conn:
                wallet.write_value(pid,"prayer_ready",0)
                for item,qty in ((124,300),(125,0),(102,9),(117,1)):
                    give(pid,item,qty)
            first=call(sock,"DailyTaskExchange",21)
            assert first.status==pb.StatusCode_OK and first.today_converted==4 and first.exchange_times_left==3
            assert call(sock,"DailyTaskExchange",21)==first
            batch=call(sock,"DailyTaskExchange",22,exchange_all=True)
            assert batch.status==pb.StatusCode_OK and batch.rewards[0].main_item.base_item.num==3
            assert call(sock,"DailyTaskExchange",23).status==pb.StatusCode_DAILY_WISH_TIMES_NOT_ENOUGH
            with host.lock:
                assert wallet.wallet(pid,124)==60 and wallet.wallet(pid,125)==4
            assert call(sock,"BuyGameCoin",31,buy_count=0).status==pb.StatusCode_BAD_REQ
            assert call(sock,"BuyGameCoin",32,buy_count=2).status==pb.StatusCode_ITEM_NOT_ENOUGH
            one=call(sock,"BuyGameCoin",33,buy_count=1)
            assert one.status==pb.StatusCode_OK and call(sock,"BuyGameCoin",33,buy_count=1)==one
            with host.lock,db.conn:
                assert wallet.wallet(pid,117)==2 and wallet.wallet(pid,102)==4
                wallet.write_value(pid,"last_coin",int(wallet.time.time())-2*10800-7)
            # Failed payment cannot undo independently due recovery or debit the residual currency.
            assert call(sock,"BuyGameCoin",34,buy_count=1).status==pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock,db.conn:
                assert wallet.wallet(pid,117)==4 and wallet.wallet(pid,102)==4
                give(pid,117,1000000)
            assert call(sock,"BuyGameCoin",35,buy_count=1).status==pb.StatusCode_FAIL
        host.stop()
        host.start()
        with patch.object(wallet,"now",return_value=date+timedelta(minutes=1)):
            sock,same=connect()
        with sock,patch.object(wallet,"now",return_value=date+timedelta(minutes=1)):
            reset=call(sock,"DailyTaskExchange",40)
            assert reset.status==pb.StatusCode_OK and reset.today_converted==1
            with host.lock:
                assert wallet.wallet(pid,125)==5 and same==pid and wallet.wallet(pid,117)==1000000
    finally:
        host.stop()
    print("PASS native prayer conversion single/all, price tiers, reset and arcade purchase, recovery and legacy over-cap wallet.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
