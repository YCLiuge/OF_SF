"""Native socket regression for numbered plazas, persisted homes, limits and automatic refunds."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import login, join_after_intro, next_msg
from test_observed_initialization import call


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    profiles=[ident.create_profile(x) for x in ("屋主","访客","邻居")]
    game=LocalGameService(ident,root,resources,port=0)
    templates=json.loads((resources/"data/PlazaMusic.json").read_bytes())["instruments"]
    t=templates[0]
    def connect(n):
        sock,auth,pid=login(game.port,ident.issue_ticket(*profiles[n]))
        main,scene=join_after_intro(sock)
        assert main.channel_label==main.channel_id==scene.data.channel_label==scene.data.channel_id
        return sock,pid,scene.data
    def switch(sock,ch,scene=9999,**extra):
        rsp=call(sock,"ChangeSceneChannel",scene_id=scene,channel_label=ch,**extra)
        assert rsp.status==pb.StatusCode_OK,(ch,rsp)
        assert rsp.channel_id==rsp.channel_label
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def put(sock,fid):
        return call(sock,"PlaceFurniture",furniture_id=fid,furniture_item_id=t,pos={"x":4,"z":5})
    def bag(pid,n=None):
        with closing(sqlite3.connect(root/"game.sqlite")) as db,db:
            blob=db.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=?",(pid,t)).fetchone()
            item=pb.ItemDetail.FromString(blob[0]);count=item.main_item.base_item.num
            if n is not None:
                item.main_item.base_item.num=n
                db.execute("UPDATE items SET item_detail_blob=? WHERE player_id=? AND item_id=?",(item.SerializeToString(),pid,t))
            return count
    game.start()
    try:
        a,aid,initial=connect(0);b,bid,_=connect(1);c,cid,_=connect(2)
        with a,b,c:
            assert aid>=1000001 and len({aid,bid,cid})==3 and initial.channel_id==1
            assert not initial.scene_garden_data.garden_furniture_info_map
            bag(aid,7);bag(bid,7)
            assert put(a,100).status==pb.StatusCode_OK and bag(aid)==6
            assert put(a,101).status!=pb.StatusCode_OK
            assert put(b,100).status==pb.StatusCode_FURNITURE_OCCUPY
            assert switch(b,1).scene_garden_data.furniture_player_map[100]==aid
            assert call(a,"ChangeSceneChannel",scene_id=9999,channel_label=100000).status!=pb.StatusCode_OK
            assert bag(aid)==6
            assert switch(a,1,scene=1).scene_id==1 and bag(aid)==7
            remove=next_msg(b,MsgId.SceneGardenFurnitureRemoveNotice,pb.SceneGardenFurnitureRemoveNotice)
            assert remove.furniture_id==100
            assert not switch(b,1).scene_garden_data.garden_furniture_info_map
            switch(a,1,scene=1)
            switch(a,2)
            assert bag(aid)==7
            assert switch(a,aid).channel_id==aid
            assert put(a,200).status==put(a,201).status==pb.StatusCode_OK
            assert call(a,"GetGardenInfo").garden_info.furniture_limit_num==500
            other=switch(b,0,target_player_label=aid)
            assert set(other.scene_garden_data.garden_furniture_info_map)=={200,201}
            assert put(b,202).status==pb.StatusCode_OK and put(b,203).status!=pb.StatusCode_OK
            assert call(b,"TakeOutFurniture",furniture_id=200,is_put_in_package=True).status!=pb.StatusCode_OK
            assert switch(c,cid).scene_garden_data.garden_furniture_info_map=={}
            assert call(a,"SwitchGardenStatus",is_open=False).status==pb.StatusCode_OK
            assert call(c,"ChangeSceneChannel",scene_id=9999,channel_label=aid).status==pb.StatusCode_SCENE_CHANNEL_PRIVATE
            assert call(a,"SwitchGardenStatus",is_open=True).status==pb.StatusCode_OK
            assert len(switch(c,aid).scene_garden_data.garden_furniture_info_map)==3
            assert switch(b,99999).channel_id==99999
            assert put(b,303).status==pb.StatusCode_OK
            assert bag(bid)==5
        game.stop();game.start()
        b,bid,scene=connect(1)
        with b:
            # The earlier cross-scene departure refunded once; reconnecting another character or restarting cannot replay it.
            assert bag(aid)==5
            assert scene.channel_id==99999 and not scene.scene_garden_data.garden_furniture_info_map and bag(bid)==6
            assert len(switch(b,aid).scene_garden_data.garden_furniture_info_map)==3
            assert call(b,"TakeOutFurniture",furniture_id=202,is_put_in_package=False).status==pb.StatusCode_OK
            assert bag(bid)==6 and put(b,202).status==pb.StatusCode_OK and bag(bid)==6
            assert call(b,"TakeOutFurniture",furniture_id=202,is_put_in_package=True).status==pb.StatusCode_OK and bag(bid)==7
            held=call(b,"HandingFurniture",item_id=t)
            assert held.status==pb.StatusCode_OK and bag(bid)==6
            assert call(b,"TakeOutHandingFurniture",furniture_id=held.furniture_id).status==pb.StatusCode_OK and bag(bid)==7
            assert call(b,"TakeOutHandingFurniture",furniture_id=held.furniture_id).status!=pb.StatusCode_OK and bag(bid)==7
        game.stop()
        def stored_public(fid):
            with closing(sqlite3.connect(root/"game.sqlite")) as db,db:
                obj=pb.FurnitureDetailsInfo(furniture_id=fid,furniture_item_id=t,pos={"x":100})
                db.execute("INSERT INTO furnitures VALUES(9999,1,?,?,?)",(aid,fid,obj.SerializeToString()))
        # A previously unversioned DB must move all public objects intact to their owner's home.
        with closing(sqlite3.connect(root/"game.sqlite")) as db,db:db.execute("DELETE FROM realm_garden_migration")
        stored_public(423)
        game.start()
        a,_,scene=connect(0)
        with a:
            assert bag(aid)==5
            assert scene.channel_id==aid and set(scene.scene_garden_data.garden_furniture_info_map)=={200,201,423}
        game.stop()
        # Simulate unscheduled power loss after a verified public placement was committed.
        stored_public(424)
        before=bag(aid)
        game.start()
        assert bag(aid)==before+1
        game.stop();game.start()
        assert bag(aid)==before+1
        game.stop()
        from dataclasses import asdict
        from realm_rules import RealmRules
        zero_rule=asdict(RealmRules(home_furniture_limit=0,visitor_furniture_limit=0))
        with ident._write() as conn:
            conn.execute("INSERT OR REPLACE INTO realm_rules VALUES(1,?)",(json.dumps(zero_rule),))
        game=LocalGameService(ident,root,resources,port=0);game.start()
        a,_,_=connect(0);b,_,_=connect(1)
        with a,b:
            switch(a,aid);switch(b,aid)
            assert call(a,"GetGardenInfo").garden_info.furniture_limit_num==0x7fffffff
            assert put(a,521).status==put(a,522).status==pb.StatusCode_OK
            assert call(b,"GetGardenInfo").garden_info.furniture_limit_num==0
            assert put(b,523).status!=pb.StatusCode_OK
    finally:game.stop()
    print("PASS 3 native players: public/home admission, ownership, channel broadcasts, 3 limits, return on channel exit/disconnect/power loss, legacy relocation, hand/bag transfers and restart.")


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as temp:run(Path(temp),ap.parse_args().resources)

