"""Exercise a retail supply chest, old alias records and gathering points in Garden Realm-Day."""
import argparse
from itertools import count
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_boxes import refresh_seconds
from realm_rules import configure
from test_game_realm import join_after_intro, login
from test_native_gacha import call
from test_native_ground_loot import encounter, kill, pick, travel
from utils import db

OK = pb.StatusCode_OK


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profile=identity.create_profile("樱庭补给")
    assert configure(root,{}).ordinary_ground_drop_percent==20
    assert configure(root/"opt-out",{"ordinary_ground_drop_percent":0}).ordinary_ground_drop_percent==0
    assert configure(root/"opt-out",{}).ordinary_ground_drop_percent==0
    realm=LocalGameService(identity,root,resources,port=0)
    seq=count(20)
    def open_box(client,index):
        return call(client,"TreasureBoxOpen",next(seq),treasure_box_index=index,box_type=0,loc_type=0)
    def take(client,index):
        return call(client,"TreasureBoxPickup",next(seq),box_index=index,box_type=0,loc_type=0,pick_index=-1)
    realm.start()
    try:
        sock,_,pid=login(realm.port,identity.issue_ticket(*profile))
        with sock:
            main,_=join_after_intro(sock)
            lead=next(c for c in main.characters if c.character_id==main.team.char1)
            travel(sock,next(seq))
            with realm.lock,db.conn:
                db.conn.execute("UPDATE players SET level=100,world_level=6 WHERE player_id=?",(pid,))
            # A seeded roll only controls material selection in this fixture;
            # 76 coins / 3005 / 192 / 2112004 was one retail opening, not a recipe.
            with patch("private_boxes.random.random",return_value=0):
                box=open_box(sock,17)
            assert box.status==OK and box.next_refresh_time
            goods=[r.main_item for r in box.items]
            counts={g.item_id:g.base_item.num for g in goods if g.HasField("base_item")}
            assert 72<=counts[101]<=90 and 1<=counts[192]<=3
            assert any(g.item_tag==8 and 1<=g.base_item.num<=3 for g in goods)
            assert any(g.WhichOneof("item") in ("weapon","armor") and
                       1<=getattr(g,g.WhichOneof("item")).level<=100 for g in goods)
            assert open_box(sock,17)==box
            assert sorted(g.main_item.item_id for g in take(sock,17).items)==sorted(g.item_id for g in goods)
            assert not take(sock,17).items and not open_box(sock,17).items
            # Templates 10102..10109 inherit 10101's 4h timer. Recover previously
            # frozen unclaimed and empty records, keeping the first roll exactly once.
            assert refresh_seconds(10101)==refresh_seconds(10102)==14400 and refresh_seconds(101001)==0
            alias=open_box(sock,28)
            assert alias.status==OK and alias.next_refresh_time
            def old_alias():
                data,=db.conn.execute("SELECT data FROM private_boxes WHERE player_id=? AND zone=1 AND dynamic=0 AND idx=28",(pid,)).fetchone()
                model=pb.TreasureBoxData.FromString(data)
                model.ClearField("next_refresh_time")
                db.conn.execute("UPDATE private_boxes SET data=?,refresh=0 WHERE player_id=? AND zone=1 AND dynamic=0 AND idx=28",
                                (model.SerializeToString(),pid))
            with realm.lock,db.conn:
                old_alias()
            restored=open_box(sock,28)
            assert list(restored.items)==list(alias.items) and restored.next_refresh_time
            assert take(sock,28).items
            with realm.lock,db.conn:
                old_alias()
            assert open_box(sock,28).items
            activities=(
                ("logging_axe_instance_id",311166090,200000101,[20200001,20200002],1),
                ("mining_hammer_instance_id",972583175,300000101,[20300001,20300002],2),
                ("collection_gloves_instance_id",1816617136,100100201,[20100201],0),
                ("insect_net_instance_id",1247713076,400100101,[20400101],3),
            )
            for pos,(tool,index,reward,items,kind) in enumerate(activities):
                result=call(sock,"CharacterGatherWeaponUpdate",next(seq),character_id=lead.character_id,
                            weapon_id=getattr(lead.character_appearance,tool))
                assert result.status==OK
                _,wrong_index,wrong_reward,_,_=activities[(pos+1)%len(activities)]
                rejected=call(sock,"Gather",next(seq),gather_item={"index":wrong_index,"reward":wrong_reward})
                assert rejected.status==pb.StatusCode_GATHER_WEAPON_TYPE_DIFFER and not rejected.items
                for lucky,amount in ((False,1),(True,3)):
                    fields={"index":index,"reward":reward,"is_lucky":lucky}
                    packet=next(seq)
                    got=call(sock,"Gather",packet,gather_item=fields)
                    assert got.status==OK and got.index==index,got
                    assert got.scene_gather_limit.scene_id==1
                    assert got.scene_gather_limit.gather_limits[0].gather_type==kind
                    assert [(r.main_item.item_id,r.main_item.base_item.num) for r in got.items]==[(i,amount) for i in items]
                    assert call(sock,"Gather",packet,gather_item=fields)==got
            # A deterministic test stream can reproduce the observed ordinary
            # reward with the default policy. Live draws remain independent.
            with patch("private_encounter_gifts.random.randrange",return_value=99):
                started=encounter(sock,next(seq),key=1075)
            assert started.status==OK and started.encounter.box_id==0
            with patch("private_ground_loot.random",Random(183)):
                dropped=kill(sock,next(seq),unit=1009)
                assert dropped.status==OK and [(d.main_item.item_id,d.main_item.base_item.num)
                       for d in dropped.drop_item.items]==[(5032001,2)]
                for unit in (1014,1205):
                    died=kill(sock,next(seq),unit=unit)
                    assert died.status==OK and not died.HasField("drop_item")
            obtained=pick(sock,next(seq),dropped.drop_item.index)
            assert obtained.status==OK and [(d.main_item.item_id,d.main_item.base_item.num) for d in obtained.items]==[(5032001,2)]
            assert not pick(sock,next(seq),dropped.drop_item.index).items
        print("PASS scene 1 supply chest 17, timer aliases, pre-upgrade unclaimed/empty saves, single settlement, "
              "four real harvest points, wrong tools, normal/lucky rewards, default 1075 drops and explicit opt-out.")
    finally:
        realm.stop()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as path:
        run(Path(path),parser.parse_args().resources.resolve())
