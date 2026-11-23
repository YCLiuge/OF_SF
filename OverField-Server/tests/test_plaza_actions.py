"""Check plaza actions over actual game sockets, inventory, retries, upgrade and restart."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import pickle
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import login, join
from test_observed_initialization import call


def run(root, resources):
    ident = LocalIdentity(root/"identity.sqlite3")
    users = [ident.create_profile(name) for name in ("广场一","广场二")]
    cfg = json.loads((resources/"data/PlazaMusic.json").read_bytes())
    unlocks = json.loads((resources/"data/PlayerUnlock.json").read_bytes())["player_unlock"]["datas"]
    with patch.dict(os.environ, {"OF_STAGE_DIAGNOSTICS":"1"}):
        game = LocalGameService(ident,root,resources,port=0)
    def connect(i):
        sock, _, pid = login(game.port,ident.issue_ticket(*users[i]))
        main, scene = join(sock)
        return sock, pid, main, scene.data
    def balance(pid, template):
        with closing(sqlite3.connect(root/"game.sqlite")) as conn:
            row=conn.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(pid,template)).fetchone()
            return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0
    game.start()
    try:
        a,pid,_,scene=connect(0)
        b,_,_,_=connect(1)
        with a,b:
            forecast = call(a,"GenericSceneB",generic_msg_id=4)
            assert forecast.status == pb.StatusCode_OK and len(forecast.params)==12
            hours=[p.int_value for p in forecast.params]
            assert all(0<=p<24 for p in hours) and hours==[(hours[0]+i)%24 for i in range(12)]
            if scene.tod_time//3600==hours[0]:
                assert int(forecast.params[0].string_value)==scene.weather_type
            assert call(a,"GenericSceneB",generic_msg_id=987).status==pb.StatusCode_BAD_REQ
            assert call(a,"ChangeMusicalItem").status==pb.StatusCode_OK
            template=cfg["instruments"][0]
            assert balance(pid,template)==1
            assert call(a,"ChangeMusicalItem",musical_item_instance_id=template).musical_item_id==template
            assert call(a,"PlayMusicNote",music_note_id=cfg["notes"][0]).status==pb.StatusCode_OK
            assert call(a,"PlayMusicNote").status==pb.StatusCode_OK
            assert call(a,"ChangeMusicalItem",musical_item_instance_id=842342).status!=pb.StatusCode_OK
            assert call(a,"ChangeMusicalItem").status==pb.StatusCode_OK
            assert call(a,"PlayMusicNote",music_note_id=cfg["notes"][0]).status!=pb.StatusCode_OK
            place = call(a,"PlaceFurniture",furniture_item_id=template,furniture_id=12000,pos={"x":12})
            assert place.status==pb.StatusCode_OK and place.furniture_details_info.pos.x==12 and balance(pid,template)==0
            moved = call(a,"PlaceFurniture",furniture_item_id=template,furniture_id=12000,pos={"x":30})
            assert moved.status==pb.StatusCode_OK and moved.furniture_details_info.pos.x==30
            assert call(a,"GetGardenInfo").garden_info.furniture_num==1
            assert call(b,"PlaceFurniture",furniture_item_id=template,furniture_id=12000).status==pb.StatusCode_FURNITURE_OCCUPY
            assert call(b,"TakeOutFurniture",furniture_id=12000,is_put_in_package=True).status!=pb.StatusCode_OK
            assert call(b,"ChangeMusicalItem",source=1,musical_item_instance_id=12000).musical_item_id==template
            assert call(a,"TakeOutFurniture",furniture_id=12000,is_put_in_package=True).status==pb.StatusCode_OK
            assert balance(pid,template)==1 and call(a,"GetGardenInfo").garden_info.furniture_num==0
            assert call(a,"TakeOutFurniture",furniture_id=12000,is_put_in_package=True).status!=pb.StatusCode_OK
            assert call(a,"SupplyBoxReward").status!=pb.StatusCode_OK
            with closing(sqlite3.connect(root/"game.sqlite")) as conn:
                conn.execute("UPDATE supply_box SET next_reward_time=1 WHERE player_id=?",(pid,))
                conn.commit()
            coins=balance(pid,101)
            claim=call(a,"SupplyBoxReward")
            assert claim.status==pb.StatusCode_OK and balance(pid,101)==coins+1000
            assert call(a,"SupplyBoxInfo").next_reward_time==claim.next_reward_time
            assert call(a,"SupplyBoxReward").status!=pb.StatusCode_OK and balance(pid,101)==coins+1000
            generated=call(a,"GamePlayReward",reward_id=1001,num=1)
            box=generated.dynamic_treasure_box_info
            assert generated.status==pb.StatusCode_OK and box.index and box.box_id==3001
            assert call(a,"GamePlayReward",reward_id=1001).dynamic_treasure_box_info.index==box.index
            assert call(b,"TreasureBoxOpen",box_type=1,treasure_box_index=box.index).status!=pb.StatusCode_OK
            assert call(a,"TreasureBoxPickup",box_type=1,box_index=box.index,pick_index=-1).status!=pb.StatusCode_OK
            opened=call(a,"TreasureBoxOpen",box_type=1,treasure_box_index=box.index)
            assert opened.status==pb.StatusCode_OK and opened.items[0].main_item.base_item.num==10
            assert call(a,"TreasureBoxOpen",box_type=1,treasure_box_index=box.index)==opened
            assert call(a,"TreasureBoxPickup",box_type=1,box_index=box.index,pick_index=0).status==pb.StatusCode_BAD_REQ
            initial=balance(pid,101)
        game.stop()
        with closing(sqlite3.connect(root/"game.sqlite")) as conn:
            conn.execute("UPDATE players SET unlock_functions=? WHERE player_id=?",(pickle.dumps([915]),pid))
            conn.commit()
        game.start()
        a,pid,main,scene=connect(0)
        with a:
            assert set(main.unlock_functions)=={915,*[row["i_d"] for row in unlocks]}
            assert balance(pid,template)==1
            single=call(a,"TreasureBoxPickup",box_type=1,box_index=box.index,pick_index=1)
            assert single.status==pb.StatusCode_OK and len(single.items)==1 and balance(pid,101)==initial+10
            rest=call(a,"TreasureBoxPickup",box_type=1,box_index=box.index,pick_index=-1)
            assert rest.status==pb.StatusCode_OK and balance(pid,101)==initial+10
            assert not call(a,"TreasureBoxPickup",box_type=1,box_index=box.index,pick_index=-1).items
            assert not call(a,"TreasureBoxOpen",box_type=1,treasure_box_index=box.index).items
            assert call(a,"GamePlayReward",reward_id=1001).dynamic_treasure_box_info.index>box.index
            err=game.stages.failures
            lines=[json.loads(i)["failure"] for i in err.read_text("ascii").splitlines()]
            assert all(len(v)==2 and all(type(n) is int for n in v) for v in lines) and [2308,pb.StatusCode_BAD_REQ] in lines
    finally:
        game.stop()
    print("PASS national weather forecast and scene clock, owned instruments and music notes, authenticated per-player box spawn/open/pickup, no repeat grants, feature unlock migration and failure log.")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)

