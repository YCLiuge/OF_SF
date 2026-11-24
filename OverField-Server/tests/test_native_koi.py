"""Anniversary voucher thresholds, actual remaining balances and native scratch codes."""
import argparse
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_gifts import claim as virtual_buy
import private_shop as shop
from realm_plaza import KOI,koi_deadline
from test_game_realm import login
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import balance,set_balance
from utils import db


def run(root,resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    one,two = [identity.create_profile(n) for n in ("锦鲤甲","锦鲤乙")]
    game = LocalGameService(identity,root,resources,port=0)
    instant = [datetime(2026,4,10,23,59,58,tzinfo=shop.CN)]
    NOTICES[MsgId.ActivityKoiNotice] = pb.ActivityKoiNotice

    def grant(pid,grid,token=None):
        with game.lock,db.conn:
            result = virtual_buy(pid,100,grid,token or uuid4().hex)
            assert result[0] == pb.StatusCode_OK
            return result

    def use(sock,n,qty=1,event=KOI):
        return rpc(sock,"ActivityKoiUseItem",n,activity_id=event,num=qty)[0]

    def pulse(sock,n):
        rpc(sock,"PlayerPing",n)
        rpc(sock,"PlayerPing",n+1)

    assert koi_deadline("2026-03-26 05:00:00","2026-04-11 23:59:59")=="2026-04-10 23:59:59"
    with patch("private_shop.now",side_effect=lambda:instant[0]):
        game.start()
        try:
            a,_,pid=login(game.port,identity.issue_ticket(*one))
            b,_,other=login(game.port,identity.issue_ticket(*two))
            with a,b:
                rpc(a,"PlayerMainData",3)
                rpc(b,"PlayerMainData",3)
                with game.lock,db.conn:
                    set_balance(pid,6044,0)
                    set_balance(other,6044,0)
                assert use(a,10,0).status==pb.StatusCode_BAD_REQ
                assert use(a,11,1,KOI+1).status==pb.StatusCode_CFG_NOT_FOUND
                assert use(a,12).status==pb.StatusCode_ITEM_NOT_ENOUGH
                token=uuid4().hex
                purchase=grant(pid,1,token) # Six virtual yuan.
                assert grant(pid,1,token)[1]==purchase[1]
                for _ in range(2):grant(pid,1)
                with game.lock: assert balance(pid,6044)==0
                got=grant(pid,1)
                status=next(msg.info[0] for key,msg in got[3] if key==MsgId.ActivityKoiNotice)
                assert (status.total_payment_num,status.next_reward_payment_num)==(24,40)
                with game.lock: assert balance(pid,6044)==1
                with patch("private_koi.randbelow",return_value=7):
                    first=use(a,13)
                    assert first.status==pb.StatusCode_OK and list(first.codes)==["00000007"]
                    assert use(a,13)==first
                    grant(other,2) # Thirty virtual yuan, one ticket.
                    assert use(b,14).status==pb.StatusCode_FAIL
                    with game.lock: assert balance(other,6044)==1
                second=use(b,14)
                assert second.status==pb.StatusCode_OK and second.codes[0]!=first.codes[0]
                grant(pid,6) # 648 virtual yuan; all further issuance is capped at thirty lifetime tickets.
                with game.lock: assert balance(pid,6044)==29
                assert use(a,15,30).status==pb.StatusCode_BAD_REQ
                pile=use(a,16,22)
                assert pile.status==pb.StatusCode_OK and len(set(pile.codes))==22
                grant(pid,2)
                with game.lock: assert balance(pid,6044)==7
                instant[0]+=timedelta(seconds=2)
                assert use(a,17).status==pb.StatusCode_Activity_Close
                with game.lock: before=balance(pid,102)
                grant(pid,2)
                with game.lock: assert balance(pid,6044)==7
                instant[0]=datetime(2026,4,12,0,0,0,tzinfo=shop.CN)
                pulse(a,18)
                with game.lock:
                    assert balance(pid,6044)==0 and balance(pid,102)==before+70
                    assert db.conn.execute("SELECT count(*) FROM koi_codes WHERE event=?",(KOI,)).fetchone()==(24,)
                pulse(a,20)
                with game.lock: assert balance(pid,102)==before+70
        finally:
            game.stop()
        instant[0]=datetime(2026,4,10,23,59,59,tzinfo=shop.CN)
        game.start()
        try:
            a,_,pid=login(game.port,identity.issue_ticket(*one))
            with a:
                _,frames=rpc(a,"PlayerMainData",23)
                frames+=rpc(a,"PlayerPing",24)[1]
                data=next(msg.info[0] for msg in frames if isinstance(msg,pb.ActivityKoiNotice))
                assert len(data.codes)==23 and data.codes[0]=="00000007"
        finally:
            game.stop()
    print("PASS native scratch vouchers: 20-yuan virtual steps, 30 tickets per person, unique codes, collision rollback, deadline, residual conversion and restart.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
