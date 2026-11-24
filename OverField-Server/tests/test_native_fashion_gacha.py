"""Run the PC fashion banner and wishlist through two real sockets, a reconnect and SQLite rollback."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
import private_fashion_gacha as fashion
from private_arcade_wallet import wallet
from private_dungeons import _table
from private_shop import CN
from realm_gacha import configure,defaults
from test_game_realm import login,join
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(s) for s in ("优选甲","优选乙")]
    # Upgrade a real pre-4000 operator configuration exactly once, preserving all owner-customized entries.
    legacy=defaults()
    legacy["pools"]=[p for p in legacy["pools"] if p["id"]!=4000]
    legacy["pools"][0]["sr_pity"]=8
    with identity._write() as conn:
        conn.execute("CREATE TABLE private_gacha_config (id INTEGER PRIMARY KEY, settings TEXT NOT NULL)")
        conn.execute("INSERT INTO private_gacha_config VALUES(1,?)",(json.dumps(legacy),))
    service=LocalGameService(identity,root,resources,port=0)
    serial=40
    def ask(sock,name,**kwargs):
        nonlocal serial
        serial+=1
        return call(sock,name,serial,**kwargs)
    def connect(i):
        sock,_,uid=login(service.port,identity.issue_ticket(*profiles[i]))
        join(sock)
        return sock,uid
    def view(sock):return {g.gacha_id:g for g in ask(sock,"GachaList").gachas}
    def choose(sock,pool,item):
        return ask(sock,"OptionalUpPoolItem",gacha_id=4000,poo_id=pool,item_id=item)
    def ten(sock):return ask(sock,"Gacha",gacha_id=4000,is_single=False)
    def set_stack(uid,t,n):db.set_item_detail(uid,make_item(t,n,uid).SerializeToString(),item_id=t)
    service.start()
    try:
        # Check every boundary against the five published categories, before the separate ten-pull guarantee.
        settings=next(entry for entry in defaults()["pools"] if entry["id"]==4000)
        labels={group:[group] for group in (40000,40001,40002,40003,40004)}
        for first,last,group in ((0,899,40000),(900,5899,40001),(5900,9399,40002),
                                 (9400,9799,40003),(9800,9999,40004)):
            for roll in (first,last):
                with patch.object(fashion.secrets,"randbelow",return_value=roll):
                    assert fashion.roll(settings["weights"],labels)==group
        a,uid=connect(0); b,bid=connect(1)
        with a,b:
            assert set(view(a))>={2000,4000,9000}
            assert configure(root)["pools"][0]["sr_pity"]==8
            weapon,limited=1103506,4200442
            assert choose(a,40002,weapon).status==pb.StatusCode_BAD_REQ
            assert choose(a,40003,1101501).status==pb.StatusCode_BAD_REQ
            assert ask(a,"OptionalUpPoolItem",gacha_id=2000,poo_id=40003,item_id=weapon).status==pb.StatusCode_BAD_REQ
            assert ask(a,"OptionalUpPoolItem",gacha_id=9000,poo_id=40003,item_id=weapon).status==pb.StatusCode_BAD_REQ
            date=datetime(2026,6,25,0,0,0,tzinfo=CN)
            with patch("private_fashion_gacha.now",return_value=date):
                result=choose(b,40004,limited)
                assert result.info.optional_up_item==limited and result.info.optional_value==5
            with patch("private_fashion_gacha.now",return_value=date.replace(month=7,day=8,hour=23,minute=59,second=59)):
                assert view(b)[4000].optional_up_item==limited
            with patch("private_fashion_gacha.now",return_value=date.replace(month=7,day=9)):
                assert not view(b)[4000].optional_up_item and choose(b,40004,limited).status==pb.StatusCode_BAD_REQ
            assert not view(a)[4000].optional_up_item
            assert choose(a,40003,weapon).info.optional_value==5
            original=view(a)
            with service.lock,db.conn:
                set_stack(uid,107,3599)
            before=wallet(uid,107)
            assert ten(a).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert ask(a,"Gacha",gacha_id=4000,is_single=True).status==pb.StatusCode_BAD_REQ and wallet(uid,107)==before
            with service.lock,db.conn:
                set_stack(uid,107,50000)
            # A 100% dye category is an operator override. The native independent ten-pull outfit floor remains.
            cfg=configure(root)
            next(p for p in cfg["pools"] if p["id"]==4000)["weights"]={"40000":10000}
            configure(root,cfg)
            with patch.object(fashion.secrets,"randbelow",side_effect=lambda n:n-1):
                pull=ten(a)
                assert pull.status==pb.StatusCode_OK
                assert [x.main_item.item_id for x in pull.items[:9]]==[401]*9
                assert pull.items[9].main_item.HasField("outfit") and len(pull.items)==10
                assert pull.info.optional_value==10 and view(a)[4000].gacha_times==10
                assert call(a,"Gacha",serial-1,gacha_id=4000,is_single=False)==pull and wallet(uid,107)==46400
                assert choose(a,40003,weapon).info.optional_value==10
                pull2=ten(a)
                assert pull2.info.optional_value==15 and wallet(uid,107)==42800
            with patch.object(fashion.secrets,"randbelow",return_value=0):
                hit=ten(a)
                assert hit.info.optional_value==5 and len(hit.items)==10 and wallet(uid,107)==39200
                wins=[x for x in hit.items if x.main_item.item_id==weapon]
                assert len(wins)==1 and db.get_item_detail(uid,instance_id=wins[0].main_item.weapon.instance_id)
                assert any(x.main_item.HasField("outfit") for x in hit.items)
            choices=fashion.groups_for(set())
            weapon_catalog=_table("Weapon","weapon")
            unknown=next(r["item_i_d"] for r in _table("Gacha","reward_pool")[40003]["items"] if r["item_i_d"] not in choices[40003])
            family=weapon_catalog[unknown]["asset_full_name"]
            same=next(c for c,r in weapon_catalog.items() if r.get("asset_full_name")==family and c!=unknown)
            assert unknown in fashion.groups_for({same})[40003] and limited in choices[40004]
            assert view(a)[2000]==original[2000] and view(a)[9000]==original[9000] and view(b)[4000].gacha_times==0
            hist=ask(a,"GachaRecord",gacha_id=4000,page=6)
            assert hist.total_page==6 and len(hist.records)==5 and all(x.gacha_id==4000 for x in hist.records)
            # An overflow after the ticket reservation must undo all grants, history and the streak.
            with service.lock,db.conn:
                set_stack(uid,401,0xffffffff)
            with patch.object(fashion.secrets,"randbelow",side_effect=lambda n:n-1):
                assert ten(a).status==pb.StatusCode_Item_Get_Fail
            assert wallet(uid,107)==39200 and view(a)[4000]==hit.info
            assert choose(b,40003,weapon).status==pb.StatusCode_OK
    finally:
        service.stop()
    service=LocalGameService(identity,root,resources,port=0)
    service.start()
    try:
        a,uid=connect(0); b,bid=connect(1)
        with a,b:
            assert view(a)[4000]==hit.info and view(b)[4000].gacha_times==0
            assert choose(a,0,0).status==pb.StatusCode_OK and not view(a)[4000].optional_value
            assert view(b)[4000].optional_up_item==weapon
            cfg=configure(root);cfg["pools"]=[p for p in cfg["pools"] if p["id"]!=4000];configure(root,cfg)
            assert 4000 not in view(a) and choose(a,40003,weapon).status==pb.StatusCode_BAD_REQ and ten(a).status==pb.StatusCode_BAD_REQ
    finally:
        service.stop()
    assert not any(p["id"]==4000 for p in configure(root)["pools"])
    print("PASS 4000 five rates, 10-only native cost, ten-pull outfit floor, per-account reset/increments, native selection windows, weapon discovery, history, rollback, replay and restart.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as t:
        run(Path(t),p.parse_args().resources)
