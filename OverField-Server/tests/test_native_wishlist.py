"""National wish-list rules and native gift mail for online and offline friends."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_friends import set_state
from private_shop import CN, balance
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    profiles = [identity.create_profile(n) for n in ("愿望甲","愿望乙")]
    realm = LocalGameService(identity,root,resources,port=0)
    realm.start()
    def enter(i):
        sock,_,pid = login(realm.port,identity.issue_ticket(*profiles[i]))
        join(sock)
        return sock,pid
    def op(sock,board,grid,kind=pb.WishListOperateType_Add,seq=20):
        return call(sock,"WishListOperate",seq,shop_id=board,grid_id=grid,operate_type=kind)
    def wish(sock,uid,board,grid):
        return next(x for x in call(sock,"WishListByFriendId",24,player_id=uid).wish_list if (x.shop_id,x.grid_id)==(board,grid))
    def send(sock,target,entry,seq):
        return call(sock,"WishListBuy",seq,player_id=target,info=entry)
    def amount(pid,key):
        detail=balance(pid,key)
        return detail.main_item.base_item.num if detail else 0
    def inbox(sock):
        return call(sock,"GetMails",40).mails
    def intimacy(a,b,points):
        for owner,peer in ((a,b),(b,a)):
            db.set_friend_info(owner,peer,"friend_intimacy",points)
    try:
        now = datetime(2025,10,12,20,tzinfo=CN)
        with patch("private_shop.now",return_value=now) as clock:
            src,giver=enter(0)
            dest,target=enter(1)
            with src,dest:
                assert op(dest,201,1).status==pb.StatusCode_OK
                assert op(dest,201,1,seq=21).status==pb.StatusCode_WISH_LIST_EXIST
                assert op(dest,201,1,kind=pb.WishListOperateType_Del,seq=22).status==pb.StatusCode_OK
                assert op(dest,201,1,kind=pb.WishListOperateType_Del,seq=23).status==pb.StatusCode_WISH_LIST_NOT_EXIST
                assert op(dest,201,1,seq=24).status==pb.StatusCode_OK
                assert op(dest,999999,1).status==pb.StatusCode_WISH_LIST_SHOP_ERR
                first=wish(dest,target,201,1)
                assert send(src,target,first,31).status==pb.StatusCode_FRIEND_NOT_EXIST
                assert send(dest,target,first,32).status==pb.StatusCode_BAD_REQ
                with realm.lock,db.conn:
                    for a,b in ((giver,target),(target,giver)):
                        set_state(a,b,pb.FriendStatus_FRIEND)
                    intimacy(giver,target,99)
                assert send(src,target,first,33).status==pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
                with realm.lock,db.conn:
                    intimacy(giver,target,100)
                    db.conn.execute("UPDATE private_friend_since SET since=?",(int(now.timestamp())-2,))
                assert send(src,target,first,34).status==pb.StatusCode_FRIEND_DAYS_NOT_ENOUGH
                with realm.lock,db.conn:
                    db.conn.execute("UPDATE private_friend_since SET since=?",(int(now.timestamp())-86400,))
                assert send(src,target,first,35).status==pb.StatusCode_OK
                assert send(src,target,first,36).status==pb.StatusCode_OK
                letters=[m for m in inbox(dest) if m.items and m.items[0].item_id==127]
                assert len(letters)==1
                b0=amount(target,127)
                assert call(dest,"OperateMails",37,operate_type=pb.MailOperateType_CLAIM,mail_id=letters[0].mail_id).status==pb.StatusCode_OK
                assert amount(target,127)==b0+1
                assert len([m for m in inbox(src) if m.title=="ROTA速达"])==1
                assert op(dest,900,1).status==pb.StatusCode_OK
                w=wish(dest,target,900,1)
                assert send(src,target,w,38).status==pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
                with realm.lock,db.conn:
                    intimacy(giver,target,1000)
                    db.set_item_detail(giver,make_item(108,100,giver).SerializeToString(),108)
                assert send(src,target,w,39).status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert wish(dest,target,900,1)==w
                with realm.lock,db.conn:
                    db.set_item_detail(giver,make_item(108,10000,giver).SerializeToString(),108)
                assert send(src,target,w,42).week_send_count==1 and amount(giver,108)==9700
                assert send(src,target,w,42).week_send_count==1 and amount(giver,108)==9700
                for index,grid in enumerate((2,3,4),start=2):
                    assert op(dest,900,grid,seq=60+grid).status==pb.StatusCode_OK
                    candidate=wish(dest,target,900,grid)
                    reply=send(src,target,candidate,50+grid)
                    if index<=3:
                        assert reply.status==pb.StatusCode_OK and reply.week_send_count==index
                    else:
                        assert reply.status==pb.StatusCode_WISH_LIST_SEND_LIMIT
                assert call(src,"WishListByShopId",63,shop_id=900,grid_id=4).week_send_count==3
                clock.return_value = datetime(2025,10,13,4,59,tzinfo=CN)
                assert send(src,target,candidate,64).status==pb.StatusCode_WISH_LIST_SEND_LIMIT
                clock.return_value += timedelta(minutes=1)
                assert send(src,target,candidate,65).week_send_count==1
                assert op(dest,900,5,seq=66).status==pb.StatusCode_OK
                pending=wish(dest,target,900,5)
            realm.stop()
            realm.start()
            src,_=enter(0)
            with src:
                old=amount(giver,108)
                r=send(src,target,pending,68)
                assert r.status==pb.StatusCode_OK and amount(giver,108)==old-980
                assert send(src,target,pending,69).status==pb.StatusCode_OK
                with realm.lock:
                    assert db.conn.execute("SELECT count(*) FROM private_wish_orders WHERE mail=0").fetchone()==(1,)
            realm.stop()
            realm.start()
            clock.return_value = datetime(2025,10,18,7,tzinfo=CN)
            dest,_=enter(1)
            with dest:
                converted=[m for m in inbox(dest) if any((a.item_id,a.num)==(102,980) for a in m.items)]
                assert len(converted)==1
                pre=amount(target,102)
                result=call(dest,"OperateMails",70,operate_type=pb.MailOperateType_CLAIM,mail_id=converted[0].mail_id)
                assert result.status==pb.StatusCode_OK and amount(target,102)==pre+980
                assert call(dest,"OperateMails",71,operate_type=pb.MailOperateType_CLAIM,mail_id=converted[0].mail_id).status==pb.StatusCode_MAIL_NO_REWARD
                clock.return_value = datetime(2026,3,25,12,tzinfo=CN)
                assert op(dest,900,63,seq=72).status==pb.StatusCode_OK
                special = wish(dest,target,900,63)
                src,_=enter(0)
                with src:
                    old=amount(giver,108)
                    with realm.lock,db.conn:
                        db.conn.execute("CREATE TRIGGER fail_gift BEFORE INSERT ON private_wish_orders BEGIN SELECT RAISE(ABORT,'test rollback'); END")
                    try:
                        send(src,target,special,73)
                    except (EOFError, ConnectionError):
                        pass
                    else:
                        raise AssertionError("Injected storage failure produced a response")
                    with realm.lock,db.conn:
                        db.conn.execute("DROP TRIGGER fail_gift")
                        assert amount(giver,108)==old
                src,_=enter(0)
                with src:
                    assert send(src,target,special,74).status==pb.StatusCode_OK
                    assert amount(giver,108)==old-1314  # regular shop price is 2,000
                from private_dungeons import _table
                from private_wishes import selection
                with realm.lock:
                    choices=[(b["i_d"],g["grid_i_d"]) for b in _table("Shop","grid").values()
                             for g in b["items"] if g.get("is_gift") and selection(target,b["i_d"],g["grid_i_d"],clock.return_value)]
                assert len(choices)>30
                for i,(board,grid) in enumerate(choices[:31]):
                    result=op(dest,board,grid,seq=80+i)
                    assert result.status==(pb.StatusCode_OK if i<30 else pb.StatusCode_WISH_LIST_LIMIT)
    finally:
        realm.stop()
    print("PASS native wish-list add/delete, friendship age/intimacy, sender payment, direct virtual offers, weekly reset, two mailboxes, retry, offline expiry conversion and restart.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
