"""National event drops, realm hit milestones and purchase-mode compatibility over native TCP."""
import argparse
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_gifts import claim as buy
import private_sandbag
from private_shop import CN
from realm_events import configure
from test_game_realm import login,join_after_intro
from test_native_events import NOTICES, rpc
from test_native_gacha_atomicity import balance,set_balance
from utils import db


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    slots=[identity.create_profile(name) for name in ("庆典甲","庆典乙")]
    game=LocalGameService(identity,root,resources,port=0)
    when=[datetime(2026,10,8,4,59,59,tzinfo=CN)]
    event,npc=260815150,10301
    NOTICES.update({getattr(MsgId,n):getattr(pb,n) for n in ("ActivitySandbagDropNotice","ActivitySandbagNotice","ActivityGiftDataNotice")})
    def connect(n):
        sock,_,pid=login(game.port,identity.issue_ticket(*slots[n]))
        join_after_intro(sock)
        return sock,pid
    def hit(s,i,n=1,target=npc):
        return rpc(s,"ActivitySandbagHit",i,activity_id=event,npc_id=target,hit_count=n)
    def reward(s,i,ids,kind=pb.RewardType_Box):
        return rpc(s,"ActivitySandbagReward",i,activity_id=event,reward_type=kind,seq_list=ids)[0]
    def gift(s,i,n=1):
        return rpc(s,"ActivityGiftReward",i,activity_id=3001,achieve_id=80300100+n)[0]
    def purchase(pid):
        with game.lock,db.conn:
            return buy(pid,10000,1,uuid4().hex)
    with patch("private_shop.now",side_effect=lambda:when[0]):
        game.start()
        try:
            a,pid=connect(0)
            b,other=connect(1)
            with a,b:
                assert hit(a,40,0)[0].status==pb.StatusCode_BAD_REQ
                assert hit(a,41,3,10302)[0].status==pb.StatusCode_BAD_REQ
                reply,frames=hit(a,42,3)
                assert reply.status==pb.StatusCode_OK and reply.hit_count==3
                drop=next(m for m in frames if isinstance(m,pb.ActivitySandbagDropNotice))
                assert len(drop.box_list)==3 and len({i.seq for i in drop.box_list})==3
                assert hit(a,42,3)[0]==reply
                key,item,qty=drop.box_list[0].seq,drop.box_list[0].item_id,drop.box_list[0].num
                assert reward(b,43,[key]).status==pb.StatusCode_BAD_REQ
                with game.lock:
                    before=balance(pid,item)
                    assert private_sandbag.total(event)==3 and private_sandbag.progress(pid,event)==(3,1)
                assert reward(a,44,[key,999999]).status==pb.StatusCode_BAD_REQ
                with game.lock: assert balance(pid,item)==before
                obtained=reward(a,45,[key])
                assert obtained.status==pb.StatusCode_OK and reward(a,45,[key])==obtained
                assert reward(a,46,[key]).status==pb.StatusCode_REPEAT_GET_REWARD
                with game.lock: assert balance(pid,item)==before+qty
                assert reward(a,47,[2],pb.RewardType_Server).status==pb.StatusCode_FAIL
                assert reward(a,48,[1],pb.RewardType_Server).status==pb.StatusCode_OK
                assert reward(a,49,[1],pb.RewardType_Server).status==pb.StatusCode_REPEAT_GET_REWARD
                with game.lock,db.conn:
                    db.conn.execute("UPDATE sandbag_totals SET hits=999999 WHERE event=?",(event,))
                when[0]+=timedelta(seconds=1)
                assert hit(b,50)[0].status==pb.StatusCode_OK
                assert reward(a,51,[2],pb.RewardType_Server).status==pb.StatusCode_OK
                assert hit(a,52,100)[0].status==pb.StatusCode_OK
                assert hit(a,53,100)[0].status==pb.StatusCode_OK
                with game.lock:
                    assert private_sandbag.progress(pid,event)==(203,2)
                    rows=db.conn.execute("SELECT slot,day,count(*) FROM sandbag_boxes WHERE player=? AND event=? GROUP BY slot,day",(pid,event)).fetchall()
                    for slot,day,count in rows:
                        cap=private_sandbag.config(event)[0]["activity_interaction_item"][slot-1]["output_ceiling"]
                        assert count<=cap
                assert gift(a,54).status==pb.StatusCode_FAIL
                status,items,_,_=purchase(pid)
                assert status==pb.StatusCode_OK and items.items
                with game.lock,db.conn:
                    gifted=balance(pid,110)
                    # Old builds saved a purchase receipt before the optional mode marker existed.
                    db.conn.execute("DELETE FROM private_activity_gift_modes WHERE player=?",(pid,))
                assert gift(a,55).status==pb.StatusCode_ActivityGift_Rewarded
                configure(root,{"events":[{"id":3001,"enabled":True,"opens_at":None,"closes_at":None,"staged_gifts":True}]})
                assert gift(a,56).status==pb.StatusCode_ActivityGift_Rewarded
                with game.lock: assert balance(pid,110)==gifted
                status,items,_,_=purchase(other)
                assert status==pb.StatusCode_OK and not items.items
                assert purchase(other)[0]==pb.StatusCode_ActivityGift_HasBuy
                assert gift(b,57,2).status==pb.StatusCode_FAIL
                first=gift(b,58)
                assert first.status==pb.StatusCode_OK and first.rewards and gift(b,58)==first
                assert gift(b,59).status==pb.StatusCode_ActivityGift_Rewarded
                when[0]+=timedelta(days=1)
                assert gift(b,60,2).status==pb.StatusCode_OK
        finally:
            game.stop()
        game.start()
        try:
            a,pid=connect(0)
            with a:
                assert reward(a,61,[key]).status==pb.StatusCode_REPEAT_GET_REWARD
                assert gift(a,62).status==pb.StatusCode_ActivityGift_Rewarded
                when[0]=datetime(2026,11,24,tzinfo=CN)
                assert hit(a,63)[0].status==pb.StatusCode_Activity_Close
        finally:
            game.stop()
    print("PASS national sandbag actor, persisted personal boxes, UTC+8 reset, actual realm milestones, multi-part rollback, retries, original gift receipts and optional staged login gifts.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
