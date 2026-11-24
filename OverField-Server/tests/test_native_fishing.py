"""Exercise fishing through the original packet entry, delayed settlement and fishing book claims."""
import argparse
from datetime import datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_crafts import base
from private_dungeons import _table,progress
import private_fishing as fishing
import private_shop as shop
from test_game_realm import login,join_after_intro
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import balance,set_balance
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    roles=LocalIdentity(root/"identity.sqlite3")
    a_role,b_role=[roles.create_profile(n) for n in ("钓手甲","钓手乙")]
    host=LocalGameService(roles,root,resources,port=0)
    seq=count(200)
    clock=[1000.0]
    date=[datetime(2026,11,24,18,0,tzinfo=shop.CN)]
    NOTICES.update({getattr(MsgId,n):getattr(pb,n) for n in ("FishingResultNotice","LifeProficiencyNotice","LifeAchieveNotice","PackNotice")})
    def ask(s,n,**f):
        return rpc(s,n,next(seq),**f)
    def opened(ident):
        sock,_,pid=login(host.port,roles.issue_ticket(*ident))
        join_after_intro(sock)
        return sock,pid
    def cast(s,**args):
        return ask(s,"Fishing",**({"fishing_pond_id":1001,"fishing_rod_id":1006001,"fishing_box_id":5021001}|args))[0]
    def pulse(s):
        frames=ask(s,"PlayerPing")[1]+ask(s,"PlayerPing")[1]
        return [m for m in frames if isinstance(m,pb.FishingResultNotice)]
    def finish(s,seconds):
        clock[0]+=seconds
        return pulse(s)
    def counts(s):
        result=ask(s,"GetOneTypeLifePictorialBookCount",life_type=fishing.KIND)[0]
        return {i.id:i.count for i in result.life_achieve_count_lst}
    def prepare(pid):
        with host.lock,db.conn:
            set_balance(pid,11,80)
            set_balance(pid,5021001,40)
    with (patch("private_fishing.monotonic",side_effect=lambda:clock[0]),patch("private_shop.now",side_effect=lambda:date[0])):
        host.start()
        try:
            a,pid=opened(a_role)
            b,other=opened(b_role)
            with a,b:
                prepare(pid)
                prepare(other)
                def chosen_pool(bait):
                    captured=[]
                    def choose(entries,weights,k):
                        captured.extend(r["i_d"] for r in entries)
                        return entries[:1]*k
                    with patch("private_fishing.random.choices",side_effect=choose),patch("private_fishing.random.random",return_value=0):
                        fishing.plan(1,bait,True,{})
                    return set(captured)
                seasonal={6022,6023,6024}
                assert not seasonal.intersection(chosen_pool(6021))
                date[0]=datetime(2026,6,12,tzinfo=shop.CN)
                assert seasonal<=chosen_pool(6021) and not seasonal.intersection(chosen_pool(5021001))
                date[0]=datetime(2026,11,24,tzinfo=shop.CN)
                assert cast(a,fishing_pond_id=111).status==pb.StatusCode_BAD_REQ
                assert cast(a,fishing_box_id=5021005).status==pb.StatusCode_LifeLvNotEnough
                assert cast(a,fishing_box_id=6021).status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert cast(a,fishing_rod_id=1004001).status==pb.StatusCode_ITEM_NOT_ENOUGH
                with host.lock,db.conn:
                    set_balance(pid,11,0)
                assert cast(a).status==pb.StatusCode_EnergyNotEnough
                prepare(pid)
                start=cast(a)
                assert start.status==pb.StatusCode_OK and cast(a).fishing_event_result==start.fishing_event_result
                assert not pulse(a) and not counts(a)
                assert ask(a,"Fishing",is_cancel=True)[0].is_cancel
                assert not finish(a,22) and not counts(a)
                assert cast(a).status==pb.StatusCode_OK
                for c in (2,1):
                    assert ask(a,"ChangeSceneChannel",scene_id=9999,channel_label=c)[0].status==pb.StatusCode_OK
                assert not finish(a,22) and not counts(a)
                assert cast(a).status==pb.StatusCode_OK
                assert ask(a,"DungeonEnter",dungeon_id=11101,char1=101001)[0].status==pb.StatusCode_OK
                assert cast(a).status==pb.StatusCode_BAD_REQ
                assert ask(a,"DungeonExit")[0].status==pb.StatusCode_OK
                assert not finish(a,22)
                with patch("private_fishing.random.random",return_value=0.0):
                    actual=cast(a).fishing_event_result
                fish=actual.fish_infos[0].i_d
                row=_table("Fishing","fishing_info")[fish]
                assert all(row["min_size"]<=f.length<=row["max_size"] for f in actual.fish_infos if f.i_d==fish)
                assert list(actual.fishing_event_list) and 0<actual.cost_time<=20
                assert not finish(a,actual.cost_time-1)
                results=finish(a,1)
                assert len(results)==1 and results[0].status==pb.StatusCode_OK and not pulse(a)
                book=counts(a)
                assert sum(book.values())==len(actual.fish_infos) and not counts(b)
                with host.lock:
                    assert (balance(pid,11),balance(pid,5021001))==(79,39)
                    assert base(pid,fishing.KIND).proficiency_value>=100
                    assert balance(pid,row["item_i_d"])>=book[fish]
                with patch("private_fishing.random.random",return_value=.999):
                    failed=cast(a).fishing_event_result
                assert not failed.fish_infos
                assert finish(a,failed.cost_time)[0].status==pb.StatusCode_OK and counts(a)==book
                with host.lock: assert progress(pid,100210102)==1
                with patch("private_fishing.random.random",return_value=0):
                    shortage=cast(a).fishing_event_result
                with host.lock,db.conn:
                    set_balance(pid,5021001,0)
                assert finish(a,shortage.cost_time)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
                with host.lock: assert balance(pid,11)==78
                assert counts(a)==book
                with host.lock,db.conn:
                    prepare_bait=make_item(1006002,1,pid)
                    instance=prepare_bait.main_item.weapon.instance_id
                    prepare_bait.main_item.weapon.durability=10
                    db.set_item_detail(pid,prepare_bait.SerializeToString(),instance_id=instance)
                    set_balance(pid,5021001,12)
                    fid=5023001
                    rule=_table("Fishing","fishing_info")[fid]
                    goal=rule["fish_achieve_rewards"][0]["achieve_condition_i_d"]
                    threshold=_table("Achieve","achieve")[goal]["count_param"]
                    db.set_achieve(pid,goal,pb.Achieve(achieve_id=goal,count=threshold-1).SerializeToString())
                with patch("private_fishing.plan") as plan:
                    preview=pb.FishingEventResult(cost_time=4,cost_endurance=7)
                    preview.fish_infos.add(i_d=fid,length=rule["min_size"])
                    plan.return_value=preview
                    chosen=cast(a,fishing_rod_id=instance)
                    assert chosen.status==pb.StatusCode_OK
                    assert finish(a,4)[0].status==pb.StatusCode_OK
                with host.lock:
                    rod=pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=instance))
                    assert rod.main_item.weapon.durability==3
                life=ask(a,"GetLifeAchieve",life_type=fishing.KIND)[0]
                assert (goal,threshold) in {(q.achieve_id,q.count) for q in life.life_achieve.life_achieve_}
                assert ask(a,"GetLifeAchievementReward",life_type=fishing.KIND,achieve_id=goal)[0].status==pb.StatusCode_OK
                assert ask(a,"GetLifeAchievementReward",life_type=fishing.KIND,achieve_id=goal)[0].status==pb.StatusCode_REPEAT_GET_REWARD
                assert ask(b,"GetLifeAchievementReward",life_type=fishing.KIND,achieve_id=goal)[0].status==pb.StatusCode_FAIL
                with host.lock,db.conn:
                    info=base(pid,fishing.KIND)
                    info.proficiency_value=_table("Fishing","fishing_level")[1]["upgrade_level_by_proficiency"]
                    db.set_life(pid,fishing.KIND,info.SerializeToString())
                    set_balance(pid,101,2500)
                assert ask(a,"LifeLeveUp",life_type=fishing.KIND)[0].life_base_info.level==2
                with host.lock:
                    assert balance(pid,101)==1500 and balance(pid,11)==77
                previous=counts(a)
                assert cast(a).status==pb.StatusCode_OK
        finally:
            host.stop()
        clock[0]+=50
        host.start()
        try:
            a,pid=opened(a_role)
            with a:
                assert not pulse(a) and counts(a)==previous
                assert goal in ask(a,"GetLifeAchieve",life_type=fishing.KIND)[0].life_achieve.rewarded_life_achieve_id_lst
                with host.lock: assert balance(pid,11)==77
        finally:
            host.stop()
    print("PASS fishing casts with original equipment, bait and ponds, native previews, delayed single settlement, cancellation, scene/connection changes, proficiency, gear wear and book claims.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
