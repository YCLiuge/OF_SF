"""Exercise fishing through the original packet entry, delayed settlement and fishing book claims."""
import argparse
from datetime import datetime
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from local_game_wire import receive
from private_crafts import base
from private_dungeons import _table,progress
import private_fishing as fishing
import private_shop as shop
from test_game_realm import login,join_after_intro
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import balance,set_balance
from utils import db
from utils.pb_create import make_item


def catch_catalog():
    catalog = _table("Fishing", "fishing_info")
    assert [int(r["fish_pond_pool"]) for r in _table("FishPond", "fish_pond").values()] == [1001]
    # All 60 bait hints from the national simplified item descriptions, grouped by recommended grade.
    preferred = {
        1: {5022005, *range(5023001,5023009), *range(5023041,5023045)},
        2: {5022006, *range(5023009,5023017), *range(5023045,5023050)},
        3: {5022007, *range(5023017,5023025), 5023050, 5023051},
        4: {5022008, *range(5023025,5023033), 5023052, 5023053},
        5: {5022009, *range(5023033,5023041), 5023054, 5023055},
    }
    assert sum(map(len,preferred.values())) == 60

    def shares(level, grade, day, skills=None, active=None):
        def record(rows, weights, k):
            total = sum(weights)
            result.update((row["i_d"], weight / total) for row,weight in zip(rows,weights))
            return rows[:1] * k
        def choose(parts):
            if isinstance(parts[0], int):
                return active if active in parts else parts[-1]
            return parts[0]
        result = {}
        with (patch("private_fishing.random.random", return_value=0),
              patch("private_fishing.random.choice", side_effect=choose),
              patch("private_fishing.random.choices", side_effect=record)):
            preview = fishing.plan(level, 5021000 + grade, day, skills or {})
        return result, [(e.i_d,e.index) for e in preview.fishing_event_list]

    for day in (True, False):
        by_grade = [shares(10, g, day)[0] for g in range(1,6)]
        for grade, fish in preferred.items():
            for fid in fish & by_grade[0].keys():
                assert by_grade[grade-1][fid] > max(d[fid] for i,d in enumerate(by_grade,1) if i != grade)
        assert 5023020 not in shares(4,3,day)[0]
        assert 5023020 in shares(5,3,day)[0]
        assert all(catalog[f].get("new_day_type",0) in (2,int(not day)) for f in by_grade[0])
    for skill, event, size in ((30400,210,1),(30500,220,2),(30600,230,3)):
        normal, no_event = shares(10,3,True,active=event)
        boosted, with_event = shares(10,3,True,skills={skill:0.05},active=event)
        assert (event,1) not in no_event and (event,1) in with_event
        # Compare total normalized probability for this size, including its ordinary fish.
        selected = [f for f in normal if catalog[f].get("new_size_type",0) == size]
        assert sum(boosted[f] for f in selected) > sum(normal[f] for f in selected)


def legacy_rod_login(host, roles, ask):
    """Exercise the original client's main-data, inventory and equip sequence on a high-ID old save."""
    template = 1006001
    hero, empty, custom, recruited = 101001, 101002, 101003, 101004
    ident = roles.create_profile("旧档钓手")
    sock, _, player = login(host.port, roles.issue_ticket(*ident))
    with sock:
        with host.lock, db.conn:
            unequipped = db.add_character(player, empty)
            unequipped.character_appearance.fishing_rod_instance_id = 0
            db.set_character(player, empty, unequipped.SerializeToString())
            selected = db.add_character(player, custom)
            upgraded = make_item(1006002, 1, player)
            upgraded_id = upgraded.main_item.weapon.instance_id
            selected.character_appearance.fishing_rod_instance_id = upgraded_id
            db.set_item_detail(player, upgraded.SerializeToString(), instance_id=upgraded_id)
            db.set_character(player, custom, selected.SerializeToString())
            # In old imports a different owned weapon may legitimately occupy the template number.
            occupied = make_item(1002001, 1, player)
            occupied.main_item.weapon.instance_id = template
            db.set_item_detail(player, occupied.SerializeToString(), instance_id=template)
            db.get_instance_id(player, minimum=template + 50)
            db.conn.execute("INSERT INTO player_kv VALUES(?,'fishing_rod_wear',?,?)", (player, str(template), 17))
        main, _ = ask(sock, "PlayerMainData")
        slots = {char.character_id: char.character_appearance.fishing_rod_instance_id for char in main.characters}
        rod_id = slots[hero]
        assert rod_id > template and slots[empty] == 0 and slots[custom] == upgraded_id
        delivered = {}
        for _ in range(100):
            header, data = receive(sock)
            if header.msg_id == MsgId.SceneDataNotice:
                break
            if header.msg_id == MsgId.PackNotice:
                for detail in pb.PackNotice.FromString(data).items:
                    if detail.main_item.HasField("weapon"):
                        w = detail.main_item.weapon
                        delivered[w.instance_id] = (w.weapon_id, w.durability)
        assert delivered[rod_id] == (template, _table("Weapon", "weapon")[template]["durability"] - 17)
        assert delivered[template][0] == 1002001 and delivered[upgraded_id][0] == 1006002
        assert ask(sock, "CharacterGatherWeaponUpdate", character_id=hero, weapon_id=rod_id)[0].status == pb.StatusCode_OK
        with host.lock, db.conn:
            # A subsequent character award must resolve to a usable shared rod in that same packet.
            from private_gacha import award
            delta, notice = pb.ItemDetail(), pb.PackNotice()
            award(player, recruited, delta, notice)
            assert delta.main_item.character.character_appearance.fishing_rod_instance_id == rod_id
            assert sum(pb.ItemDetail.FromString(b).main_item.weapon.weapon_id == template
                       for b in db.get_item_detail(player, table="items_s")) == 1
    fresh, _, same_player = login(host.port, roles.issue_ticket(*ident))
    with fresh:
        assert same_player == player
        repeat, _ = ask(fresh, "PlayerMainData")
        saved = next(c for c in repeat.characters if c.character_id == hero)
        assert saved.gather_weapon == saved.character_appearance.fishing_rod_instance_id == rod_id
        with host.lock:
            rebuilt = pb.ItemDetail.FromString(db.get_item_detail(player, instance_id=rod_id))
            assert rebuilt.main_item.weapon.durability == delivered[rod_id][1]


def run(root,resources):
    roles=LocalIdentity(root/"identity.sqlite3")
    a_role,b_role=[roles.create_profile(n) for n in ("钓手甲","钓手乙")]
    from realm_rules import configure
    configure(root, {"activity_fragment_percent": 100})
    host=LocalGameService(roles,root,resources,port=0)
    seq=count(200)
    clock=[1000.0]
    date=[datetime(2026,11,24,18,0,tzinfo=shop.CN)]
    NOTICES.update({getattr(MsgId,n):getattr(pb,n) for n in ("FishingResultNotice","LifeProficiencyNotice","LifeAchieveNotice","PackNotice","ChatMsgNotice")})
    def ask(s,n,**f):
        return rpc(s,n,next(seq),**f)
    def opened(ident):
        sock,_,pid=login(host.port,roles.issue_ticket(*ident))
        main,_=join_after_intro(sock)
        leader=next(char for char in main.characters if char.character_id==main.team.char1)
        rod_id=leader.character_appearance.fishing_rod_instance_id
        equip=ask(sock,"CharacterGatherWeaponUpdate",character_id=leader.character_id,weapon_id=rod_id)[0]
        assert rod_id and equip.status==pb.StatusCode_OK, (rod_id,equip.status)
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
            legacy_rod_login(host, roles, ask)
            a,pid=opened(a_role)
            b,other=opened(b_role)
            with a,b:
                prepare(pid)
                prepare(other)
                with host.lock,db.conn:
                    life=base(pid,fishing.KIND)
                    life.level=10
                    db.set_life(pid,fishing.KIND,life.SerializeToString())
                    set_balance(pid,11,776)
                    set_balance(pid,5021002,6076)
                    twins=[make_item(1006010,1,pid) for _ in range(2)]
                    assert all(t.main_item.weapon.durability==1500 for t in twins)
                    twins[1].main_item.weapon.durability=0
                    for t in twins:
                        db.set_item_detail(pid,t.SerializeToString(),instance_id=t.main_item.weapon.instance_id)
                args=dict(fishing_pond_id=1001,fishing_rod_id=1006010,fishing_box_id=5021002)
                assert ask(a,"CharacterGatherWeaponUpdate",character_id=101001,weapon_id=twins[1].main_item.weapon.instance_id)[0].status==pb.StatusCode_OK
                denied,notes=ask(a,"Fishing",**args)
                assert denied.status==pb.StatusCode_FAIL and any(isinstance(n,pb.ChatMsgNotice) and n.type==pb.ChatChannel_System and "耐久度已耗尽" in n.msg.text for n in notes)
                assert not any(isinstance(n,pb.ChatMsgNotice) for n in ask(b,"PlayerPing")[1])
                with host.lock:
                    assert (balance(pid,11),balance(pid,5021002))==(776,6076)
                assert ask(a,"CharacterGatherWeaponUpdate",character_id=101001,weapon_id=twins[0].main_item.weapon.instance_id)[0].status==pb.StatusCode_OK
                with patch("private_fishing.random.random",return_value=.999):
                    accepted=ask(a,"Fishing",**args)[0]
                assert accepted.status==pb.StatusCode_OK
                empty=accepted.fishing_event_result
                assert not empty.fish_infos and empty.cost_endurance==0
                assert finish(a,empty.cost_time)[0].status==pb.StatusCode_OK
                with host.lock,db.conn:
                    assert (balance(pid,11),balance(pid,5021002))==(775,6075)
                    assert pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=twins[0].main_item.weapon.instance_id)).main_item.weapon.durability==1500
                    life.level=1
                    db.set_life(pid,fishing.KIND,life.SerializeToString())
                prepare(pid)
                with host.lock:
                    fragments_before = balance(pid, 124)
                catch_catalog()
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
                # Required levels 1/3/5/7/7 are printed on the five national bait boxes.
                with host.lock,db.conn:
                    info=base(pid,fishing.KIND)
                    info.level=6
                    db.set_life(pid,fishing.KIND,info.SerializeToString())
                for bait in (5021003,5021004,5021005):
                    expected=pb.StatusCode_ITEM_NOT_ENOUGH if bait==5021003 else pb.StatusCode_LifeLvNotEnough
                    assert cast(a,fishing_box_id=bait).status==expected
                with host.lock,db.conn:
                    info.level=7
                    db.set_life(pid,fishing.KIND,info.SerializeToString())
                for bait in (5021004,5021005):
                    assert cast(a,fishing_box_id=bait).status==pb.StatusCode_ITEM_NOT_ENOUGH
                with host.lock,db.conn:
                    info.level=1
                    db.set_life(pid,fishing.KIND,info.SerializeToString())
                assert cast(a,fishing_box_id=6021).status==pb.StatusCode_ITEM_NOT_ENOUGH
                assert cast(a,fishing_rod_id=1004001).status==pb.StatusCode_GATHER_WEAPON_TYPE_DIFFER
                assert cast(a,fishing_rod_id=0).status==pb.StatusCode_GATHER_WEAPON_NOT_WEAR
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
                    assert balance(pid,124) == fragments_before + 5
                    assert base(pid,fishing.KIND).proficiency_value>=100
                    assert balance(pid,row["item_i_d"])>=book[fish]
                with patch("private_fishing.random.random",return_value=.999):
                    failed=cast(a).fishing_event_result
                assert not failed.fish_infos
                assert finish(a,failed.cost_time)[0].status==pb.StatusCode_OK and counts(a)==book
                with host.lock:
                    assert balance(pid,124) == fragments_before + 5
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
                with host.lock,db.conn:
                    info = base(other,fishing.KIND)
                    info.level = 9
                    db.set_life(other,fishing.KIND,info.SerializeToString())
                    set_balance(other,5021003,12)
                    tool = make_item(1006002,1,other)
                    tool.main_item.weapon.durability = 40
                    rod_id = tool.main_item.weapon.instance_id
                    db.set_item_detail(other,tool.SerializeToString(),instance_id=rod_id)
                def settle_cast(other_fish):
                    def fixed_event(parts):
                        if isinstance(parts[0],int):
                            return parts[-1]
                        # Use the five-point cast wear and the two-catch ending together.
                        return parts[-1] if parts[-1].get("desciption_i_d")==302 else parts[0]
                    def select_fish(rows,weights,k):
                        selected = next(r for r in rows if r["i_d"] == other_fish)
                        return [selected]*k
                    with (patch("private_fishing.random.choice",side_effect=fixed_event),
                          patch("private_fishing.random.random",return_value=0 if other_fish else .999),
                          patch("private_fishing.random.choices",side_effect=select_fish)):
                        resp = cast(b,fishing_box_id=5021003,fishing_rod_id=rod_id)
                    assert resp.status == pb.StatusCode_OK
                    preview = resp.fishing_event_result
                    events=[(e.i_d,e.index) for e in preview.fishing_event_list]
                    assert preview.cost_endurance == (5 if other_fish else 0)
                    assert ((100,1) in events)==bool(other_fish)
                    assert bool(other_fish) or ((100,3) in events and all(event!=300 for event,_ in events))
                    assert [f.i_d for f in preview.fish_infos] == ([other_fish]*2 if other_fish else [])
                    if other_fish == 5023020:
                        assert all(170 <= f.length <= 240 for f in preview.fish_infos)
                    results = finish(b,preview.cost_time)
                    assert len(results)==1 and results[0].status==pb.StatusCode_OK and not pulse(b)
                    return results[0]
                for i,(landed,large_count) in enumerate(((5023020,0),(5022001,0),(5023006,2),(5023006,4),(0,4)),1):
                    result = settle_cast(landed)
                    with host.lock:
                        assert progress(other,100210901) == large_count
                        assert balance(other,11) == 80-i and balance(other,5021003) == 12-i
                        wear = pb.ItemDetail.FromString(db.get_item_detail(other,instance_id=rod_id)).main_item.weapon.durability
                        assert wear == 40-5*min(i,4)
                    assert any(item.main_item.weapon.instance_id==rod_id for item in result.items if item.main_item.HasField("weapon"))==bool(landed)
                assert counts(b) == {5023020:2,5022001:2,5023006:4}
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
            b,other=opened(b_role)
            with b:
                assert counts(b)=={5023020:2,5022001:2,5023006:4}
                with host.lock:
                    assert progress(other,100210901)==4 and balance(other,5021003)==7
                    assert pb.ItemDetail.FromString(db.get_item_detail(other,instance_id=rod_id)).main_item.weapon.durability==20
        finally:
            host.stop()
    configure(root,{"fishing_min_level":7})
    host=LocalGameService(roles,root,resources,port=0)
    host.start()
    try:
        for profile,expected in ((roles.create_profile("新钓手"),7),(a_role,7),(b_role,9)):
            client,_=opened(profile)
            with client:
                assert ask(client,"GetLifeInfo",life_type=fishing.KIND)[0].life_base_info.level==expected
                assert ask(client,"GetLifeInfo",life_type=pb.LIFE_TYPE_Cooking)[0].life_base_info.level==1
    finally:
        host.stop()
    configure(root,{"fishing_min_level":1})
    host=LocalGameService(roles,root,resources,port=0)
    host.start()
    try:
        for profile,expected in ((a_role,7),(roles.create_profile("开荒钓手"),1)):
            client,_=opened(profile)
            with client:
                assert ask(client,"GetLifeInfo",life_type=fishing.KIND)[0].life_base_info.level==expected
    finally:
        host.stop()
    print("PASS fishing level floors, new and returning players, bait gates 1/3/5/7/7.")
    print("PASS native fishing: 60 recommended bait grades, day/night and seasonal eligibility, mastery, "
          "five-point wear, arapaima, empty hooks, large-fish goal, repeat casts, cancellation, "
          "connection changes, persisted gear and atlas claims.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
