"""Probe the real private gift catalog and native game messages without a payment network."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg
from test_observed_initialization import call
from utils import db
from private_shop import balance
import private_gifts


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    accounts=[identity.create_profile(n) for n in ("礼包测试甲","礼包测试乙")]
    game=LocalGameService(identity,root,resources,port=0)
    def connect(i):
        sock,_,pid=login(game.port,identity.issue_ticket(*accounts[i]))
        join(sock)
        return sock,pid
    def coins(pid):
        with game.lock:
            detail=balance(pid,108)
            return detail.main_item.base_item.num if detail else 0
    def click(sock, grid, packet):
        req=pb.CreatePayOrderReq(shop_id=100,grid_id=grid)
        sock.sendall(encode(MsgId.CreatePayOrderReq,req.SerializeToString(),packet))
        response=next_msg(sock,MsgId.CreatePayOrderRsp,pb.CreatePayOrderRsp)
        assert response.status==pb.StatusCode_Not_Payment and not response.order_id and not response.result_str
        return next_msg(sock,MsgId.PaySendGoodsNotice,pb.PaySendGoodsNotice)
    game.start()
    try:
        first,player=connect(0)
        other,second=connect(1)
        with first,other:
            original,unrelated=coins(player),coins(second)
            result=click(first,1,32)
            assert result.shop_id==100 and result.grids.grid_id==1 and coins(player)==original+120
            click(first,1,32)
            assert coins(player)==original+120 and coins(second)==unrelated
            click(first,1,33)
            assert coins(player)==original+180
            # Targeted foreign gifts and a fabricated pool cannot modify either balance.
            for invalid in ({"to_player_id":second},{"pool_id":999999}):
                reply=call(first,"CreatePayOrder",shop_id=100,grid_id=1,**invalid)
                assert reply.status!=pb.StatusCode_OK and not reply.order_id
            with game.lock,db.conn:
                catalog=private_gifts.catalog()
                assert len(catalog)>=100 and all(row["title"] and row["section"] for row in catalog)
                bad=[]
                for entry in catalog:
                    status,delivery,_,_=private_gifts.claim(player,entry["shop"],entry["grid"],uuid4().hex)
                    if status!=pb.StatusCode_OK or not delivery.items:
                        bad.append((entry["shop"],entry["grid"],status))
                assert not bad,bad
                receipt=uuid4().hex
                assert private_gifts.claim(player,100,2,receipt)[0]==pb.StatusCode_OK
                consistent=coins(player)
            assert call(first,"MonthCardReward").status==pb.StatusCode_OK
            assert call(first,"MonthCardReward").status==pb.StatusCode_MOON_CARD_DAYS_LIMIT
        game.stop()
        game.start()
        first,player=connect(0)
        with first,game.lock,db.conn:
            assert private_gifts.claim(player,100,2,receipt)[0]==pb.StatusCode_OK
            assert coins(player)==consistent
            assert private_gifts.claim(player,100,3,receipt)[0]==pb.StatusCode_BAD_REQ
        print(f"PASS {len(catalog)} native private gift listings, first bonuses, daily card, replay, isolation and restart.")
    finally:
        game.stop()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
