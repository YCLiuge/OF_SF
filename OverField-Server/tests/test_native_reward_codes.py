"""Run native redemption for two players, a failed inventory write and a hot withdrawal."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from realm_admin import execute
from test_game_realm import login,join_after_intro
from test_native_gacha import call
from private_arcade_wallet import wallet
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    users=[identity.create_profile(n) for n in ("兑换甲","兑换乙")]
    host=LocalGameService(identity,root,resources,port=0)
    def owner(name,**params):
        return execute(root,"codes."+name,params,None)
    def connect(user):
        sock,_,pid=login(host.port,identity.issue_ticket(*user))
        join_after_intro(sock)
        return sock,pid
    def issue(code,quota,items,**extra):
        return owner("issue",code=code,quota=quota,rewards=[dict(item=k,quantity=q) for k,q in items],**extra)
    issue("ONE",1,[(101,33)])
    issue("FULL",0,[(101,5)])
    issue("OLD",0,[(101,1)],expires_at="2001-01-01 00:00:00")
    host.start()
    try:
        a,pa=connect(users[0])
        b,pb_id=connect(users[1])
        with a,b:
            complaint=dict(report_status=pb.ChatViolation,reported_player_id=pb_id,reason="测试提交",chat_content="测试消息")
            report=call(a,"Report",14,**complaint)
            assert report.status==pb.StatusCode_OK and call(a,"Report",14,**complaint)==report
            assert call(b,"Report",15,**complaint).status==pb.StatusCode_BAD_REQ
            assert call(a,"Report",16,report_status=pb.PhotoShare,code=99999).status==pb.StatusCode_BAD_REQ
            reports= execute(root,"reports.list",{"limit":2},None)["reports"]
            assert len(reports)==1 and reports[0]["reporter"]==pa and reports[0]["target"]==pb_id
            assert reports[0]["content"]=="测试消息"
            execute(root,"reports.resolve",{"id":reports[0]["id"]},None)
            assert not execute(root,"reports.list",{},None)["reports"]
            assert len(execute(root,"reports.list",{"resolved":True},None)["reports"])==1
            start={p:wallet(p,101) for p in (pa,pb_id)}
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes=list(workers.map(lambda s:call(s,"UseRedemptionCode",21,code="one"),(a,b)))
            assert sorted(r.status for r in outcomes)==sorted((pb.StatusCode_OK,pb.StatusCode_REDEMPTION_CODE_NUM_IS_MAX))
            for (sock,uid),reply in zip(((a,pa),(b,pb_id)),outcomes):
                if reply.status==pb.StatusCode_OK:
                    assert reply.rewards[0].main_item.base_item.num==33 and wallet(uid,101)==start[uid]+33
                    assert call(sock,"UseRedemptionCode",21,code="one")==reply
                    assert call(sock,"UseRedemptionCode",22,code="ONE").status==pb.StatusCode_REDEMPTION_CODE_USED
                else:
                    assert wallet(uid,101)==start[uid]
            assert call(a,"UseRedemptionCode",24,code="OLD").status==pb.StatusCode_REDEMPTION_CODE_NOT_EXIST
            with host.lock,db.conn:
                db.set_item_detail(pa,make_item(101,0xffffffff,pa).SerializeToString(),101)
            assert call(a,"UseRedemptionCode",25,code="FULL").status!=pb.StatusCode_OK
            with host.lock,db.conn:
                db.set_item_detail(pa,make_item(101,0,pa).SerializeToString(),101)
            assert call(a,"UseRedemptionCode",26,code="FULL").status==pb.StatusCode_OK and wallet(pa,101)==5
            owner("withdraw",code="FULL")
            assert call(b,"UseRedemptionCode",27,code="FULL").status==pb.StatusCode_REDEMPTION_CODE_NOT_EXIST
            assert any(r["code"]=="FULL" and not r["enabled"] for r in owner("list")["codes"])
        host.stop()
        host.start()
        s,uid=connect(users[0])
        with s:
            assert call(s,"UseRedemptionCode",30,code="one").status in (pb.StatusCode_REDEMPTION_CODE_USED,pb.StatusCode_REDEMPTION_CODE_NUM_IS_MAX)
            assert uid==pa and wallet(uid,101)==5
    finally:
        host.stop()
    print("PASS operator-issued native codes, limit race, receipt replay, expiry, rollback, withdrawal and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
