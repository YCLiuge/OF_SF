"""Exercise all national mailbox operations with two characters over the private native protocol."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_shop import balance
from test_game_realm import join_after_intro, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    accounts = [identity.create_profile(n) for n in ("邮箱甲", "邮箱乙")]
    realm = LocalGameService(identity, root, resources, port=0)
    realm.start()

    def enter(account):
        sock, _, pid = login(realm.port, identity.issue_ticket(*account))
        join_after_intro(sock)
        return sock, pid

    def box(sock):
        result = call(sock, "GetMails", 16)
        assert result.status == pb.StatusCode_OK
        return {m.mail_id: m for m in result.mails}

    def op(sock, kind, mail=0, packet=20):
        return call(sock, "OperateMails", packet, operate_type=kind, mail_id=mail)

    def add(pid, key, rewards=(), *, expired=False, read=False, starred=False):
        mail = pb.MailBriefData(mail_id=key, title="附件测试", send_time=int(time.time())-86400*3 if expired else int(time.time()),
                                overdue_day=2, collect_status=starred, mail_state=pb.MailState_READED if read else pb.MailState_UNREAD)
        for k, n in rewards:
            mail.items.add(item_id=k,num=n)
        db.set_mail(pid,key,mail.SerializeToString())
        return mail

    def amount(pid,key):
        item = balance(pid,key)
        return item.main_item.base_item.num if item else 0

    try:
        a, pid = enter(accounts[0])
        b, other = enter(accounts[1])
        with a,b:
            welcome = box(a)[1]
            assert welcome.items and box(b)[1].mail_state == pb.MailState_UNREAD
            assert op(a,pb.MailOperateType_READ,1).mails[0].mail_state == pb.MailState_UNCLAIM
            with realm.lock, db.conn:
                db.del_mail(pid,1)
                add(pid,21,[(101,12),(1,7)])
                add(pid,22,read=True)
                add(pid,23,[(102,19)],expired=True)
                add(pid,24,[(101,3)],read=True,starred=True)
                add(other,21,[(101,42)])
                before = amount(pid,101)
                before_exp = db.get_players_info(pid,"level,exp")
                weapon = make_item(1101104,1,pid)
                weapon.main_item.weapon.strength_level = 6
                old_instance = weapon.main_item.weapon.instance_id
                db.set_item_detail(pid,weapon.SerializeToString(),instance_id=old_instance)
                mail = add(pid,25)
                mail.reward.add().CopyFrom(weapon)
                db.set_mail(pid,25,mail.SerializeToString())
                add(pid,26)
            assert 23 not in box(a)
            assert op(a,pb.MailOperateType_CLAIM,23).status==pb.StatusCode_MAIL_NOT_EXIST
            assert op(a,pb.MailOperateType_CLAIM,26).status==pb.StatusCode_MAIL_NO_REWARD
            assert op(a,pb.MailOperateType_COLLECT,21,30).collect_status
            assert op(a,pb.MailOperateType_COLLECT,21,30).collect_status
            assert not op(a,pb.MailOperateType_COLLECT,21,31).collect_status
            result = op(a,pb.MailOperateType_CLAIM,21)
            assert result.status == pb.StatusCode_OK and result.mails[0].mail_state == pb.MailState_CLAIMED
            assert {v.main_item.item_id:v.main_item.base_item.num for v in result.claim_result} == {101:12,1:7}
            notice = next_msg(a, MsgId.PackNotice, pb.PackNotice)
            assert any(v.main_item.item_id==101 and v.main_item.base_item.num==before+12 for v in notice.items)
            with realm.lock:
                assert tuple(db.get_players_info(pid,"level,exp")) == (before_exp[0],before_exp[1]+7), (before_exp,db.get_players_info(pid,"level,exp"))
            assert op(a,pb.MailOperateType_CLAIM,21).status == pb.StatusCode_MAIL_NO_REWARD
            assert op(a,pb.MailOperateType_READ,21).mails[0].mail_state == pb.MailState_CLAIMED
            with realm.lock,db.conn:
                add(pid,29,[(101,8),(0xffffffff,4)])
            assert op(a,pb.MailOperateType_CLAIM_ALL).status == pb.StatusCode_CFG_NOT_FOUND
            with realm.lock, db.conn:
                assert amount(pid,101)==before+12 and box_state(pid,24)==pb.MailState_READED
                db.del_mail(pid,29)
            all_result=op(a,pb.MailOperateType_CLAIM_ALL)
            assert all_result.status == pb.StatusCode_OK and {m.mail_id for m in all_result.mails}=={24,25}
            copied=next(r for r in all_result.claim_result if r.main_item.WhichOneof("item")=="weapon")
            assert copied.main_item.weapon.strength_level==6 and copied.main_item.weapon.instance_id!=old_instance
            assert op(a,pb.MailOperateType_CLAIM_ALL).status==pb.StatusCode_OK
            assert set(op(a,pb.MailOperateType_DELETE_ALL_READED).deleted_mail_ids)=={21,22,25}
            assert set(box(a))=={24,26}
            assert box(b)[21].mail_state==pb.MailState_UNREAD
            with realm.lock, db.conn:
                assert amount(pid,101)==before+15
                assert db.get_item_detail(pid,instance_id=old_instance)==weapon.SerializeToString()
                assert db.get_item_detail(pid,instance_id=copied.main_item.weapon.instance_id)==copied.SerializeToString()
                for key in range(101,130):
                    add(pid,key,starred=True)
            assert op(a,pb.MailOperateType_COLLECT,26,32).status == pb.StatusCode_MAIL_COLLECT_NUM_LIMIT
        realm.stop()
        realm.start()
        a, same=enter(accounts[0])
        with a:
            assert pid == same and box(a)[24].collect_status and box(a)[24].mail_state==pb.MailState_CLAIMED
            assert not op(a,pb.MailOperateType_CLAIM_ALL).claim_result
            assert amount(pid,101)==before+15
    finally:
        realm.stop()
    print("PASS five native mail operations, individual/bulk delivery, XP, rolled equipment, reliable retry, rollback, favourites limit, account isolation and restart.")


def box_state(pid, key):
    return pb.MailBriefData.FromString(db.get_mail(pid,key)).mail_state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as root:
        run(Path(root), parser.parse_args().resources)
