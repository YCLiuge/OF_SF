"""Original home scheme requests on two real role sessions, with durable and atomic inventory exchange."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from realm_rules import configure
from test_game_realm import login, join, next_msg
from test_native_gacha import call
from utils import db


def run(root, resources):
    import json
    configure(root, {"home_furniture_limit": 3})
    ident = LocalIdentity(root / "identity.sqlite3")
    profiles = [ident.create_profile(n) for n in ("设计师","同区访客")]
    game = LocalGameService(ident,root,resources,port=0)
    a,b,c = json.loads((resources/"data/PlazaMusic.json").read_bytes())["instruments"][:3]
    points = {r["item_i_d"]:r.get("home_item_point_num",0)
              for r in json.loads((resources/"data/HomeItem.json").read_bytes())["home_item"]["datas"]}

    def connect(profile):
        sock,_,uid = login(game.port,ident.issue_ticket(*profile))
        join(sock)
        return sock,uid

    def visit(s, home):
        assert call(s,"ChangeSceneChannel",5,channel_label=home,scene_id=9999).status==pb.StatusCode_OK
        return next_msg(s,MsgId.SceneDataNotice,pb.SceneDataNotice).data.scene_garden_data

    def place(s, item, fid, x):
        return call(s,"PlaceFurniture",6,furniture_id=fid,furniture_item_id=item,
                    pos={"x":x,"y":4,"z":32},rot={"y":75},layer_num=2)

    def batch(s):
        return next_msg(s,MsgId.GardenFurnitureBatchUpdateNotice,pb.GardenFurnitureBatchUpdateNotice)

    def balance(pid,item,amount=None):
        with game.lock, db.conn:
            detail=pb.ItemDetail.FromString(db.get_item_detail(pid,item))
            if amount is not None:
                detail.main_item.base_item.num=amount
                db.set_item_detail(pid,detail.SerializeToString(),item_id=item)
            return detail.main_item.base_item.num

    def save(s,slot): return call(s,"GardenFurnitureSave",10,scheme_id=slot)
    def apply(s,slot): return call(s,"GardenFurnitureApplyScheme",11,scheme_id=slot)
    def clear(s): return call(s,"GardenFurnitureRemoveAll",12)
    def rename(s,slot,name):
        request=pb.GardenFurnitureSchemeSetNameReq(scheme_id=slot,name=name)
        s.sendall(encode(MsgId.GardenFurnitureSchemeSetNameReq,request.SerializeToString(),13))
        return next_msg(s,MsgId.GardenFurnitureSchemeSetNameRsp,pb.GardenFurnitureSchemeSetNameRsp)
    def preview(s,slot): return call(s,"GardenSchemeFurnitureList",14,scheme_id=slot)
    def slots(s): return call(s,"GardenFurnitureScheme",15)
    ok=pb.StatusCode_OK

    game.start()
    try:
        owner,pid=connect(profiles[0])
        peer,uid=connect(profiles[1])
        with owner,peer:
            assert clear(owner).status==save(owner,0).status==pb.StatusCode_BAD_REQ
            visit(owner,pid)
            visit(peer,pid)
            balance(pid,a,2)
            balance(pid,b,1)
            balance(uid,c,1)
            assert slots(owner).schemes=={0:"",1:"",2:""}
            assert slots(peer).status==preview(peer,0).status==clear(peer).status==pb.StatusCode_BAD_REQ
            assert apply(owner,0).status==pb.StatusCode_BAD_REQ
            assert rename(owner,0," 夜间 ").name=="夜间"
            assert rename(owner,3,"越界").status==rename(owner,2,"\x01").status==pb.StatusCode_BAD_REQ
            assert save(owner,2).status==ok  # a deliberately empty layout is distinct from an unused slot
            for item,fid,x in ((a,300,40),(a,301,45),(b,302,93)):
                assert place(owner,item,fid,x).status==ok
            info = call(owner, "GetGardenInfo", 301).garden_info
            assert info.furniture_num == 2*points[a]+points[b] and info.furniture_limit_num == 200000
            from unittest.mock import patch
            with patch("private_home_capacity.maximum", return_value=info.furniture_num):
                assert place(peer,c,401,19).status == pb.StatusCode_FurnitureNumLimit
            assert save(owner,0).status==ok and save(owner,3).status==pb.StatusCode_BAD_REQ
            assert Counter(preview(owner,0).furniture_item_id)=={a:2,b:1}
            assert clear(owner).status==ok
            notice=batch(peer)
            assert notice.player_id==pid and not notice.new_furniture_list and notice.furniture_point_num==0
            assert clear(owner).status==ok
            batch(peer)
            assert (balance(pid,a),balance(pid,b))==(2,1)
            assert place(peer,c,300,14).status==ok
            with patch("private_home_capacity.maximum", return_value=points[c]):
                assert apply(owner,0).status==pb.StatusCode_FurnitureNumLimit
                assert balance(pid,a)==2 and balance(pid,b)==1
            before=visit(owner,pid)
            balance(pid,b,0)
            assert apply(owner,0).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert balance(pid,a)==2 and visit(owner,pid)==before
            balance(pid,b,1)
            assert apply(owner,0).status==ok
            notice=batch(peer)
            expected=points[a]*2+points[b]+points[c]
            assert notice.furniture_point_num==expected and len(notice.new_furniture_list)==3
            assert {i.pos.x for i in notice.new_furniture_list}=={40,45,93}
            assert all(i.rotation.y==75 and i.layer_num==2 and i.furniture_id!=300 for i in notice.new_furniture_list)
            assert apply(owner,0).status==ok and batch(peer)==notice
            assert (balance(pid,a),balance(pid,b),balance(uid,c))==(0,0,0)
            garden=visit(owner,pid)
            assert garden.furniture_current_point_num==expected and garden.furniture_player_map[300]==uid
            assert len(garden.garden_furniture_info_map)==4
            visit(peer,uid)
            assert all(not v for v in slots(peer).schemes.values())
            assert not preview(peer,0).furniture_item_id
        game.stop()
        configure(root,{"home_furniture_limit":2})
        game=LocalGameService(ident,root,resources,port=0)
        game.start()
        owner,again=connect(profiles[0])
        with owner:
            assert again==pid and slots(owner).schemes[0]=="夜间"
            assert apply(owner,0).status==pb.StatusCode_FurnitureNumLimit
            assert (balance(pid,a),balance(pid,b))==(0,0)
            assert apply(owner,2).status==ok
            garden=visit(owner,pid)
            assert garden.furniture_player_map=={300:uid} and garden.furniture_current_point_num==points[c]
            assert (balance(pid,a),balance(pid,b),balance(uid,c))==(2,1,0)
            assert not preview(owner,2).furniture_item_id and len(preview(owner,0).furniture_item_id)==3
            assert clear(owner).status==ok and balance(pid,a)==2
    finally:
        game.stop()
    print("PASS native three-slot garden names, preview, save, inventory exchange, visitor collision and batch notices, remove-all, retry, cap, fund refusal, role isolation and restart.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources)
