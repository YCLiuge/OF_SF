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
    operator_delivery(root/"operator-mail", resources)
    print("PASS five native mail operations, individual/bulk delivery, XP, rolled equipment, reliable retry, rollback, favourites limit, account isolation and restart.")


def operator_delivery(root, resources):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    import realm_accounts
    import realm_admin
    from local_game_wire import encode

    def send(scope, title, **extra):
        return dict(scope=scope,title=title,body="来自服主的邮件",request_id=str(uuid4()),**extra)

    def admin(name, data):
        realm_admin.validate(data, realm_admin.OPERATIONS[name]["inputSchema"], "payload")
        return realm_admin.run(root, name, data, lambda: None)

    root.mkdir()
    pre=send("existing","空服快照")
    assert admin("mail.send",pre)["recipients_at_issue"]==0
    identity=LocalIdentity(root/"identity.sqlite3")
    realm_accounts.initialize(identity)
    def create(label):
        actor=identity.create_profile(label)
        realm_accounts.register(identity,dict(username="qa"+label,password="local-test-only",nickname=label,
                                              slot=actor[0],profile_key=actor[1]),True)
        return actor
    a,b=create("邮箱甲"),create("邮箱乙")
    service=LocalGameService(identity,root,resources,port=0)
    service.start()
    def open_player(actor):
        sock,_,pid=login(service.port,identity.issue_ticket(*actor))
        join_after_intro(sock)
        return sock,pid
    def inbox(sock):
        return {m.title:m for m in call(sock,"GetMails",17).mails if m.sender=="区服管理"}
    def claim(sock,msg):
        return call(sock,"OperateMails",22,operate_type=pb.MailOperateType_CLAIM,mail_id=msg.mail_id)
    def amount(pid,item):
        found = balance(pid,item)
        return found.main_item.base_item.num if found else 0
    try:
        sock,pid=open_player(a)
        with sock:
            assert not inbox(sock)
            only=send("account","定向",username="qa邮箱甲",rewards=[{"item":101,"quantity":7}])
            with ThreadPoolExecutor(max_workers=6) as pool:
                receipts=list(pool.map(lambda _:admin("mail.send",only),range(8)))
            assert len({r["id"] for r in receipts})==1 and receipts[0]["recipients_at_issue"]==1
            try:admin("mail.send",{**only,"body":"冲突"})
            except ValueError:pass
            else:raise AssertionError("Changed replay accepted")
            for bad in (send("account","账号缺失",username="qaunknown"),
                        send("existing","非法模板",rewards=[{"item":4294967295,"quantity":1}]),
                        send("ongoing","非法收件人",username="qa邮箱甲")):
                try:admin("mail.send",bad)
                except ValueError:pass
                else:raise AssertionError("Invalid dispatch committed")
            existing=send("existing","已有玩家",rewards=[{"item":102,"quantity":9}])
            ongoing=send("ongoing","含新玩家",days=2)
            assert admin("mail.send",existing)["recipients_at_issue"]==2
            assert admin("mail.send",ongoing)["recipients_at_issue"]==2
            sock.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq().SerializeToString(),20))
            assert next_msg(sock,MsgId.ReceiveMailNotice,pb.ReceiveMailNotice).mail.title=="定向"
            mails=inbox(sock);assert set(mails)=={"定向","已有玩家","含新玩家"}
            before=amount(pid,101)
            assert claim(sock,mails["定向"]).status==pb.StatusCode_OK
            assert claim(sock,mails["定向"]).status==pb.StatusCode_MAIL_NO_REWARD
            assert amount(pid,101)==before+7
            assert call(sock,"OperateMails",24,operate_type=pb.MailOperateType_CLAIM_ALL).status==pb.StatusCode_OK
            assert not call(sock,"OperateMails",25,operate_type=pb.MailOperateType_CLAIM_ALL).claim_result
            assert call(sock,"OperateMails",26,operate_type=pb.MailOperateType_DELETE_ALL_READED).status==pb.StatusCode_OK
            assert "定向" not in inbox(sock)
        expired=send("account","已过期",username="qa邮箱乙",days=1,rewards=[{"item":101,"quantity":4}])
        expired_id=admin("mail.send",expired)["id"]
        with identity._write() as conn:
            conn.execute("UPDATE realm_mail_batches SET issued=issued-172800 WHERE id=?",(expired_id,))
        other,_=open_player(b)
        with other:assert set(inbox(other))=={"已有玩家","含新玩家"}
        future,_=open_player(create("邮箱丙"))
        with future:assert set(inbox(future))=={"含新玩家"}
        service.stop();service.start()
        sock,same=open_player(a)
        with sock:
            assert pid==same and amount(pid,101)==before+7 and set(inbox(sock))=={"含新玩家"}
            assert admin("mail.send",only)["id"]==receipts[0]["id"]
            assert set(inbox(sock))=={"含新玩家"}
        history=admin("mail.list",{"limit":2})["batches"]
        assert [h["title"] for h in history]==["已过期","含新玩家"]
        assert history[0]["rewards"]==[{"item":101,"quantity":4}]
        assert len(admin("mail.list",{"before":history[-1]["id"]})["batches"])==3
    finally:
        service.stop()
    print("PASS operator mail native online notice, offline delivery, new-player policy, concurrent retry, expiry window, claims, deletion and restart.")

def box_state(pid, key):
    return pb.MailBriefData.FromString(db.get_mail(pid,key)).mail_state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as root:
        run(Path(root), parser.parse_args().resources)
