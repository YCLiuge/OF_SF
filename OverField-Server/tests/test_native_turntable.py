"""Exercise the retail capsule notices, first-single price, ten-draw refund, gifts and history."""
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from private_shop import CN
from realm_events import available, configure
from test_game_realm import login
from test_native_events import NOTICES, rpc
from test_native_gacha_atomicity import balance, set_balance
from utils import db


def run(root, resources):
    pid_a = 7001
    identity = LocalIdentity(root/"identity.sqlite3")
    accounts = [identity.create_profile(s) for s in ("扭蛋甲","扭蛋乙")]
    game = LocalGameService(identity, root, resources, port=0)
    all_wheels = [e for e in available() if e["kind"] == "turntable"]
    assert len(all_wheels)==50 and len({r["name"] for r in all_wheels})==28
    NOTICES[MsgId.ActivityTurntableDataNotice] = pb.ActivityTurntableDataNotice
    override = {"theme":1, "activities":[pid_a], "arcade":0,
                "opens_at":"2026-11-01 00:00:00", "closes_at":"2026-11-30 00:00:00"}
    configure(root,override,plaza=True)
    def connect(index):
        sock,_,pid=login(game.port,identity.issue_ticket(*accounts[index]))
        _,msg=rpc(sock,"PlayerMainData",10)
        _,later=rpc(sock,"PlayerPing",11)
        wheel=next(w for frame in msg+later if isinstance(frame,pb.ActivityTurntableDataNotice)
                   for w in frame.activities if w.activity_id==pid_a)
        return sock,pid,wheel
    def pull(s,seq,n=1):
        return rpc(s,"ActivityTurntableDraw",seq,activity_id=pid_a,draw_times=n)
    def history(s,seq,page):
        return rpc(s,"ActivityTurntableRecord",seq,activity_id=pid_a,page=page)[0]
    def choose_normal(items,**kw):
        return [next((i for i in items if not i.get("is_special")),items[0])]
    def choose_theme(items,**kw):
        return [next((i for i in items if i.get("is_special")),items[0])]
    when = datetime(2026,11,16,12,0,tzinfo=CN)
    with patch("private_shop.now",return_value=when):
        game.start()
        try:
            sock,pid,wheel = connect(0)
            peer,other,_ = connect(1)
            with sock,peer:
                assert wheel.single_discount and sum(s.remain_count for s in wheel.items.values())==45
                assert pull(sock,12,3)[0].status==pb.StatusCode_BAD_REQ
                with game.lock, db.conn:
                    set_balance(pid,126,40)
                    set_balance(pid,108,5)
                assert pull(sock,13)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert not history(sock,14,1).records
                with patch("private_turntable.random.choices",side_effect=choose_normal):
                    assert len(pull(sock,15,10)[0].reward_indexes)==10
                    with game.lock,db.conn:
                        assert balance(pid,126)==30
                        set_balance(pid,108,6)
                    discounted, notices = pull(sock,16)
                    assert discounted.status==pb.StatusCode_OK
                    assert any(not m.activities[0].single_discount for m in notices if isinstance(m,pb.ActivityTurntableDataNotice))
                    assert pull(sock,16)[0]==discounted
                    with game.lock:
                        assert balance(pid,108)==0 and balance(pid,126)==30
                assert history(sock,17,1).total_page==2 and len(history(sock,18,2).records)==1
                assert not history(peer,19,1).records
                with game.lock:
                    old={s.item_id:balance(pid,s.item_id) for s in wheel.items.values()
                         if _table("Item","item")[s.item_id]["new_bag_item_tag"]!=pb.EBagItemTag_Fashion}
                    seen=dict(db.conn.execute("SELECT slot,count(*) FROM private_wheel_draws WHERE player=? AND event=? GROUP BY slot",(pid,pid_a)))
                with patch("private_turntable.random.choices",side_effect=choose_theme):
                    result, notices=pull(sock,20,10)
                    assert result.status==pb.StatusCode_OK and len(result.reward_indexes)==3
                    assert result==pull(sock,20,10)[0]
                assert any(all(i.remain_count==0 for i in m.activities[0].items.values())
                           for m in notices if isinstance(m,pb.ActivityTurntableDataNotice))
                with game.lock:
                    gains=Counter()
                    for index,s in wheel.items.items():
                        if s.item_id in old:
                            gains[s.item_id] += (s.remain_count-seen.get(index,0))*s.item_num
                    assert all(balance(pid,i)==v+gains[i] for i,v in old.items())
                    assert balance(pid,126)==27
                assert pull(sock,21)[0].status==pb.StatusCode_TURNTABLE_NO_REWARD_REMAIN
                # A late inventory overflow rolls back earlier item grants, currency and history.
                with game.lock,db.conn:
                    set_balance(other,108,6)
                    set_balance(other,101,0xffffffff)
                select_gold=lambda choices,**kw:[next(i for i in choices if i["item_i_d"]==101)]
                with patch("private_turntable.random.choices",side_effect=select_gold):
                    assert pull(peer,22)[0].status==pb.StatusCode_Item_Get_Fail
                with game.lock: assert balance(other,108)==6 and balance(other,101)==0xffffffff
                assert not history(peer,23,1).records
                # Open one authored edition of every template and complete the same
                # native single-draw action for each, with one discount per edition.
                editions = {e["name"]:e["id"] for e in all_wheels}
                configure(root,{**override,"activities":list(editions.values())},plaza=True)
                with game.lock,db.conn:
                    set_balance(other,108,6*len(editions))
                    set_balance(other,101,0)
                with patch("private_turntable.random.choices",side_effect=choose_theme):
                    for number,edition in enumerate(editions.values(),100):
                        result,_ = rpc(peer,"ActivityTurntableDraw",number,activity_id=edition,draw_times=1)
                        assert result.status==pb.StatusCode_OK and len(result.reward_indexes)==1, edition
                with game.lock:
                    assert balance(other,108)==0
                configure(root,override,plaza=True)
        finally:
            game.stop()
        game.start()
        try:
            sock,pid,restored=connect(0)
            with sock:
                assert not any(i.remain_count for i in restored.items.values())
                assert len(history(sock,24,2).records)==4
                configure(root,{},plaza=True)
                assert pull(sock,25)[0].status==pb.StatusCode_Activity_Close
                assert history(sock,26,1).total_page==2
        finally:
            game.stop()
    print("PASS 28 capsule templates across 50 editions, native notices, first-single discount, ten tickets, exhaustion, automatic completion gifts, early-stop refund, atomic rollback, independent records and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
