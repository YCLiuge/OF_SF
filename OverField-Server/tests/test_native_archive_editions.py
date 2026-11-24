"""Exercise the named capsule machines, passes, recruit and two-seat boutique furniture over native TCP."""
import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

import realm_admin
from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from private_shop import CN
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from test_native_gacha_atomicity import balance, set_balance
from utils import db


BOOK = 5043915


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    slots=[identity.create_profile(n) for n in ("典藏甲","典藏乙")]
    game=LocalGameService(identity,root,resources,port=0)
    clock=[datetime(2026,11,15,12,tzinfo=CN)]

    def admin(method, data=None):
        return realm_admin.execute(root,method,data or {},lambda:None)
    def day(yyyymmdd):
        clock[0]=datetime.fromisoformat(yyyymmdd+"T12:30:00").replace(tzinfo=CN)
    def go_home(sock,owner,n):
        switched=call(sock,"ChangeSceneChannel",n,channel_label=owner,scene_id=9999)
        assert switched.status==pb.StatusCode_OK
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def sit(sock,packet,fid,index):
        return call(sock,"SceneSitChair",packet,chair_id=fid,seat_id=index,is_sit=True)

    with patch("private_shop.now",side_effect=lambda:clock[0]):
        game.start()
        try:
            player,_,pid=login(game.port,identity.issue_ticket(*slots[0]))
            peer,_,friend=login(game.port,identity.issue_ticket(*slots[1]))
            with player,peer:
                join(player)
                join(peer)
                dates={3003,103014,203014}
                catalog={r["id"]:r for r in admin("gacha.catalog")["banners"]}
                assert all(catalog[i]["featured"]==402002 and catalog[i]["pity_group"]==3003 for i in dates)
                others={p["id"]:p for p in admin("gacha.get")["pools"] if p["id"]!=203014}
                enabled=admin("gacha.enable",{"id":203014})
                assert {p["id"]:p for p in enabled["pools"] if p["id"]!=203014}==others
                featured=next(p for p in enabled["pools"] if p["id"]==203014)
                featured["weights"]={"3003":10000}  # Deterministic local fixture.
                admin("gacha.set",enabled)
                with game.lock,db.conn:
                    set_balance(pid,110,2)
                    db.conn.execute("DELETE FROM characters WHERE player_id=? AND character_id=402002",(pid,))
                assert 203014 in {r.gacha_id for r in call(player,"GachaList",10).gachas}
                r=call(player,"Gacha",11,gacha_id=203014,is_single=True)
                assert r.status==pb.StatusCode_OK and r.items[0].main_item.character.character_id==402002
                assert r.items[0].main_item.is_new and db.get_characters(pid,402002)
                assert call(player,"Gacha",11,gacha_id=203014,is_single=True)==r
                dupe=call(player,"Gacha",12,gacha_id=203014,is_single=True)
                assert dupe.status==pb.StatusCode_OK and dupe.items[0].extras and balance(pid,110)==0
                assert len(call(player,"GachaRecord",13,gacha_id=203014,page=1).records)==2

                with game.lock,db.conn:
                    set_balance(pid,108,24)
                for n,(edition,machine,stamp) in enumerate(((260430010,7022,"2026-05-15"),
                        (260514012,7023,"2026-06-15"),(260715018,7022,"2026-11-15"),
                        (260715019,7023,"2026-11-15")),20):
                    day(stamp)
                    assert _table("Activity","activity")[edition]["activity_i_d"]==machine
                    result=call(player,"ActivityTurntableDraw",n,activity_id=edition,draw_times=1)
                    assert result.status==pb.StatusCode_OK and len(result.reward_indexes)==1 and result.rewards
                    records=call(player,"ActivityTurntableRecord",n+10,activity_id=edition,page=1).records
                    assert len(records)==1 and records[0].index==result.reward_indexes[0]
                    assert not call(peer,"ActivityTurntableRecord",n+10,activity_id=edition,page=1).records
                assert balance(pid,108)==0
                day("2026-12-01")
                assert call(player,"ActivityTurntableDraw",40,activity_id=260715018,draw_times=1).status==pb.StatusCode_Activity_Close

                with game.lock,db.conn:
                    set_balance(pid,102,100)
                for n,(edition,template,stamp) in enumerate(((260514070,10140,"2026-06-15"),(260625070,10150,"2026-07-15")),45):
                    day(stamp)
                    levels=_table("BattlePass","battle_pass")[template]["battle_pass_level_ups"]
                    assert len(levels)==70
                    daily=call(player,"GetActivityBattlePassQuestReward",n,activity_battle_pass_id=edition,achieve_id=80200100)
                    assert daily.status==pb.StatusCode_OK and daily.add_exp==200
                    bought=call(player,"ActivityBattlePassBuyExp",n+5,activity_battle_pass_id=template)
                    assert bought.status==pb.StatusCode_OK and bought.battle_pass_lv>=1
                    tier=call(player,"GetActivityBattlePassLevelReward",n+10,activity_battle_pass_id=edition,level=1,is_get_free_reward=True)
                    assert tier.status==pb.StatusCode_OK
                    duplicate=call(player,"GetActivityBattlePassLevelReward",n+15,activity_battle_pass_id=edition,level=1,is_get_free_reward=True)
                    assert duplicate.status==pb.StatusCode_BattlePass_LvRewarded

                with game.lock,db.conn:
                    db.conn.execute("DELETE FROM items WHERE player_id IN (?,?) AND item_id=?",(pid,friend,BOOK))
                    set_balance(pid,108,1314)
                    set_balance(friend,108,2000)
                for n,(stamp,grid,pool) in enumerate((("2026-03-12",62,961),("2026-03-20",63,962),
                        ("2026-08-19",74,973),("2026-08-27",75,974)),70):
                    day(stamp)
                    listings=call(player,"ShopInfo",n,shop_id=900).grids
                    assert any(r.grid_id==grid and r.pool_id==pool for r in listings)
                    if grid==62 or grid==75:
                        who=player if grid==62 else peer
                        actor=pid if grid==62 else friend
                        receipt=call(who,"ShopBuy",n+10,shop_id=900,grid_id=grid,buy_times=1,item_id=BOOK)
                        assert receipt.status==pb.StatusCode_OK and receipt.items[0].main_item.item_id==BOOK
                        assert balance(actor,108)==0 and balance(actor,BOOK)==1
                day("2026-11-26")
                assert not any(r.grid_id in (62,63,74,75) for r in call(player,"ShopInfo",85,shop_id=900).grids)
                go_home(player,pid,86)
                book=call(player,"PlaceFurniture",87,furniture_item_id=BOOK,pos={"x":12,"z":32},rot={"y":75},layer_num=1)
                assert book.status==pb.StatusCode_OK
                fid=book.furniture_details_info.furniture_id
                first=sit(player,88,fid,0)
                assert first.status==pb.StatusCode_OK and first.is_sit
                snapshot=go_home(peer,pid,89)
                assert snapshot.scene_garden_data.furniture_current_point_num==1685
                assert any(seat.player_id==pid and seat.chair_id==fid and seat.seat_id==0 for seat in snapshot.chair_info_list)
                assert sit(peer,90,fid,0).status==pb.StatusCode_ChairSeatTaken
                second=sit(peer,91,fid,1)
                assert second.status==pb.StatusCode_OK,(fid,pb.StatusCode.Name(second.status))
                guest=next_msg(player,MsgId.SceneSitChairRsp,pb.SceneSitChairRsp)
                assert guest.is_sit and guest.player_id==friend and guest.seat_id==1 and guest.chair_id==fid
                assert call(player,"GardenFurnitureSave",92,scheme_id=0).status==pb.StatusCode_OK
        finally:
            game.stop()
    # Simulate a realm created before placed furniture had a separate ID range.
    with closing(sqlite3.connect(root/"game.sqlite")) as legacy, legacy:
        legacy.execute("DELETE FROM realm_garden_migration WHERE version=2")
        raw,=legacy.execute("SELECT furniture_detail_blob FROM furnitures WHERE scene_id=9999 AND channel_id=? AND furniture_id=?",
                            (pid,fid)).fetchone()
        old=pb.FurnitureDetailsInfo.FromString(raw)
        old.furniture_id=3
        legacy.execute("UPDATE furnitures SET furniture_id=3,furniture_detail_blob=? WHERE scene_id=9999 AND channel_id=? AND furniture_id=?",
                       (old.SerializeToString(),pid,fid))
        scheme=pb.GardenFurnitureBatchUpdateNotice(new_furniture_list=[old])
        legacy.execute("UPDATE realm_garden_schemes SET layout=? WHERE player=? AND slot=0",(scheme.SerializeToString(),pid))
        legacy.execute("INSERT INTO garden_residents VALUES(?,?,3,1)",(pid,402002))
        legacy.execute("INSERT INTO realm_hand_furniture VALUES(?,1,?)",(friend,BOOK))
    fixed=frozenset(range(1,84))
    for reboot in range(2):
        game.start()
        try:
            player,_,_=login(game.port,identity.issue_ticket(*slots[0]))
            with player:
                _,scene=join(player)
                home=scene.data
                current=next(iter(home.scene_garden_data.garden_furniture_info_map))
                assert current not in fixed and home.scene_garden_data.furniture_current_point_num==1685
                assert any(c.character_id==402002 and c.furniture_id==current and c.seat_id==1 for c in home.placed_characters)
                applied=call(player,"GardenFurnitureApplyScheme",10,scheme_id=0)
                assert applied.status==pb.StatusCode_OK
                assert sit(player,11,3,1).status==pb.StatusCode_BAD_REQ  # Fixed plaza chair 3 has just one seat.
                assert sit(player,12,current,1).status==pb.StatusCode_ChairSeatTaken
                assert sit(player,13,current,0).status==pb.StatusCode_OK
                assert call(player,"PlaceFurniture",14,furniture_id=3,furniture_item_id=BOOK).status==pb.StatusCode_FURNITURE_OCCUPY
                if reboot==0:
                    migrated=current
                    peer,_,_=login(game.port,identity.issue_ticket(*slots[1]))
                    with peer:
                        join(peer)
                        held=call(peer,"HandingFurniture",15,item_id=BOOK)
                        assert held.status==pb.StatusCode_OK and held.furniture_id not in fixed
                        assert call(peer,"TakeOutHandingFurniture",16,furniture_id=held.furniture_id).status==pb.StatusCode_OK
                else:
                    assert current==migrated
        finally:
            game.stop()
    print("PASS both editions of the two themed wheels, pass XV/XVI native tasks and tier claims, LOLOMAY featured grant/duplicate and boutique book purchases, placement and two-seat occupancy; old furniture, resident, preset and held item ID migration across restarts.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
