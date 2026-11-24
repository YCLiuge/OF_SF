"""Verify level caps, both six-tier recovery schedules, authoritative inventory and the game-day boundary."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_shop import CN, balance
from test_game_realm import join, login
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    accounts=[identity.create_profile("补给甲"),identity.create_profile("补给乙")]
    game=LocalGameService(identity,root,resources,port=0)
    serial=50
    def connect(account):
        sock,_,player=login(game.port,identity.issue_ticket(*account))
        join(sock)
        return sock,player
    def send(sock,name,packet_id=None):
        nonlocal serial
        serial+=1
        wire_id=packet_id or serial
        request=getattr(pb,name+"Req")()
        sock.sendall(encode(getattr(MsgId,name+"Req"),request.SerializeToString(),wire_id))
        inventory={}
        for _ in range(256):
            head,raw=receive(sock)
            if head.msg_id==MsgId.PackNotice:
                for changed in pb.PackNotice.FromString(raw).items:
                    item=changed.main_item
                    if item.WhichOneof("item")=="base_item":
                        inventory[item.item_id]=item.base_item.num
            if head.msg_id==getattr(MsgId,name+"Rsp") and head.packet_id==wire_id:
                return getattr(pb,name+"Rsp").FromString(raw),inventory
        raise AssertionError("Missing recovery reply")
    def set_amount(pid,item,count):
        with game.lock,db.conn:
            db.set_item_detail(pid,make_item(item,count,pid).SerializeToString(),item)
    def amount(pid,item):
        with game.lock:
            value=balance(pid,item)
            return value.main_item.base_item.num if value else 0

    before=datetime(2026,11,24,23,59,tzinfo=CN)
    just_before=datetime(2026,11,25,4,59,59,tzinfo=CN)
    just_after=datetime(2026,11,25,5,0,tzinfo=CN)
    game.start()
    try:
        a,pid=connect(accounts[0])
        b,other=connect(accounts[1])
        with a,b,patch("private_recovery.now",return_value=before):
            with game.lock,db.conn:
                db.conn.execute("UPDATE players SET level=1 WHERE player_id=?",(pid,))
            set_amount(pid,102,0)
            for item,query,buy,counter,costs,restore,max1,limit in (
                (10,"PlayerVitality","PlayerVitalityBuy","vitality_buy_num",(50,100,100,150,200,200),60,120,pb.StatusCode_VITALITY_BUY_NUM_LIMIT),
                (11,"PlayerEnergyInfo","PlayerEnergyBuy","energy_buy_num",(30,40,40,50,200,200),200,600,pb.StatusCode_EnergyBuyTimesLimit),
            ):
                set_amount(pid,item,0)
                set_amount(pid,102,0)
                missing,changes=send(a,buy)
                assert missing.status==pb.StatusCode_ITEM_NOT_ENOUGH and getattr(missing,counter)==0 and not changes
                assert amount(pid,item)==0 and amount(pid,102)==0
                set_amount(pid,102,10000)
                for k,cost in enumerate(costs):
                    # Other gameplay may spend the stat between buys. The native purchase itself uses real inventory.
                    start=max1-1 if k==2 else 0
                    set_amount(pid,item,start)
                    funds=amount(pid,102)
                    packet=300+20*item+k
                    reply,changes=send(a,buy,packet)
                    expected=min(max1,start+restore)
                    assert reply.status==pb.StatusCode_OK and getattr(reply,counter)==k+1
                    assert reply.items[0].main_item.base_item.num==expected
                    assert changes=={102:funds-cost,item:expected}
                    assert amount(pid,102)==funds-cost and amount(pid,item)==expected
                    dup,changes=send(a,buy,packet)
                    assert dup==reply and not changes and amount(pid,102)==funds-cost
                set_amount(pid,item,0)
                funds=amount(pid,102)
                rejected,changes=send(a,buy)
                assert rejected.status==limit and getattr(rejected,counter)==6 and not changes and amount(pid,102)==funds
                status,_=send(a,query)
                assert status.status==pb.StatusCode_OK and getattr(status,counter)==6
                current=status.items[0] if item==10 else status.energy_item
                assert current.main_item.item_id==item and current.main_item.base_item.num==0
                assert getattr(send(b,query)[0],counter)==0
            # UTC+8 midnight is not the local daily cutoff.
            with patch("private_recovery.now",return_value=just_before):
                assert send(a,"PlayerVitality")[0].vitality_buy_num==6
                assert send(a,"PlayerEnergyInfo")[0].energy_buy_num==6
        game.stop()
        game.start()
        a,again=connect(accounts[0])
        with a,patch("private_recovery.now",return_value=just_before):
            assert again==pid and send(a,"PlayerVitality")[0].vitality_buy_num==6
            assert send(a,"PlayerEnergyInfo")[0].energy_buy_num==6
            with patch("private_recovery.now",return_value=just_after):
                assert send(a,"PlayerVitality")[0].vitality_buy_num==0
                assert send(a,"PlayerEnergyInfo")[0].energy_buy_num==0
                with game.lock,db.conn:
                    db.conn.execute("UPDATE players SET level=50 WHERE player_id=?",(pid,))
                for item,buy,limit in ((10,"PlayerVitalityBuy",150),(11,"PlayerEnergyBuy",800)):
                    set_amount(pid,item,limit+20)
                    funds=amount(pid,102)
                    rejected,pack=send(a,buy)
                    assert rejected.status!=pb.StatusCode_OK and not pack and amount(pid,item)==limit+20 and amount(pid,102)==funds
                    set_amount(pid,item,limit-1)
                    got,pack=send(a,buy)
                    assert got.status==pb.StatusCode_OK and pack[item]==limit
        a,_=connect(accounts[0])
        with a,patch("private_recovery.now",return_value=just_after):
            set_amount(pid,10,0)
            funds=amount(pid,102)
            persist=db.set_item_detail
            def fail_second(player,blob,item,*args):
                if player==pid and item==10:raise ValueError("isolated rollback check")
                persist(player,blob,item,*args)
            with patch.object(db,"set_item_detail",side_effect=fail_second):
                try:
                    send(a,"PlayerVitalityBuy")
                    raise AssertionError("Recovery completed after an injected failed write")
                except (EOFError,OSError):
                    pass
        a,_=connect(accounts[0])
        with a,patch("private_recovery.now",return_value=just_after):
            assert amount(pid,102)==funds and amount(pid,10)==0
            assert send(a,"PlayerVitality")[0].vitality_buy_num==1

        clock=[1900000000]
        with patch("private_recovery.seconds",side_effect=lambda:clock[0]):
            with game.lock,db.conn:
                db.conn.execute("UPDATE players SET level=50 WHERE player_id=?",(pid,))
            set_amount(pid,10,150)
            set_amount(pid,10,110)
            set_amount(pid,11,850)
            sock,_=connect(accounts[0])
            with sock:
                clock[0]+=599
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==110
                clock[0]+=1
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==111
                clock[0]+=599
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==111
            game.stop()
            game.start()
            clock[0]+=1
            sock,_=connect(accounts[0])
            with sock:
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==112
                assert send(sock,"PlayerEnergyInfo")[0].energy_item.main_item.base_item.num==850
                clock[0]+=100*600
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==150
                set_amount(pid,10,174)
                clock[0]+=7200
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==174
                set_amount(pid,10,149)
                clock[0]+=599
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==149
                clock[0]+=1
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==150
                set_amount(pid,10,40)
                with game.lock,db.conn:
                    db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='recovery_clock'",(pid,))
                clock[0]+=864000
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==40
                clock[0]+=600
                assert send(sock,"PlayerVitality")[0].items[0].main_item.base_item.num==41
    finally:
        game.stop()
    print("PASS native energy and vitality: 12 price tiers, level caps, full/insufficient guards, wallet notices, retry, isolation, 05:00 reset, 600s natural vitality, old saves, restart and atomic rollback.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
