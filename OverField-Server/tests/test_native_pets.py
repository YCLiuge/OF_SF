"""Two native roles exercise capturing, deploying, renaming and releasing private pets."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from local_game_wire import encode, receive
from private_pets import capacity
from private_shop import balance
from test_game_realm import login, join, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    auth=LocalIdentity(root/"identity.sqlite3")
    accounts=[auth.create_profile("宠物甲"),auth.create_profile("宠物乙")]
    game=LocalGameService(auth,root,resources,port=0)
    def enter(index):
        sock,_,pid=login(game.port,auth.issue_ticket(*accounts[index]))
        main,_=join(sock)
        return sock,pid,main
    def stock(pid,template,amount):
        db.set_item_detail(pid,make_item(template,amount,pid).SerializeToString(),template)
    def amount(pid,template):
        item=balance(pid,template)
        return item.main_item.base_item.num if item else 0
    def hunt(sock,seq,tool=200100007,**changes):
        args=dict(monster_id=1001,hp=10,max_hp=76,catcher_id=tool,catch_area_id=1001)
        return call(sock,"PetCapture",seq,**{**args,**changes})
    def rename(sock,seq,pet_instance_id,name):
        req=pb.PetRenameReq(pet_instance_id=pet_instance_id,name=name)
        sock.sendall(encode(MsgId.PetRenameReq,req.SerializeToString(),seq))
        for _ in range(128):
            h,raw=receive(sock)
            if h.msg_id==MsgId.PetRenameRsp and h.packet_id==seq:
                return pb.PetRenameRsp.FromString(raw)
        raise AssertionError("PetRename")
    def release(sock,seq,*ids):
        return call(sock,"PetDecompose",seq,pet_instance_ids=list(ids))
    def scene_pet(peer, name):
        for _ in range(64):
            notice=next_msg(peer,MsgId.ServerSceneSyncDataNotice,pb.ServerSceneSyncDataNotice)
            for user in notice.data:
                for entry in user.server_data:
                    if entry.action_type==pb.SceneActionType_UPDATA_PET and entry.player.pet_name==name:
                        return entry.player.pet_item_id
        raise AssertionError("No pet scene update")
    quantum=200100007
    ordinary=200100001
    bag=200100000
    game.start()
    try:
        a,pid,_=enter(0)
        b,peer,_=enter(1)
        with a,b:
            for sock in (a,b):
                assert call(sock,"ChangeSceneChannel",4,scene_id=1,channel_label=1).status==pb.StatusCode_OK
            with game.lock,db.conn:
                for template,qty in ((quantum,5),(ordinary,2),(bag,2)):
                    stock(pid,template,qty)
            assert capacity(pid)==capacity(peer)==100
            assert hunt(b,10).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert hunt(a,11,monster_id=999999).status==pb.StatusCode_BAD_REQ
            assert hunt(a,12,tool=101).status==pb.StatusCode_BAD_REQ
            assert hunt(a,13,catch_area_id=3001).status==pb.StatusCode_BAD_REQ
            assert hunt(a,14,hp=77).status==pb.StatusCode_BAD_REQ
            assert amount(pid,quantum)==5
            with patch("private_pets.random.random",return_value=.99):
                failed=hunt(a,15,ordinary)
                assert failed.status==pb.StatusCode_OK and not failed.success and not failed.items
                assert hunt(a,15,ordinary)==failed and amount(pid,ordinary)==1
                got=hunt(a,16)
            assert got.status==pb.StatusCode_OK and got.success and len(got.items)==1
            pet=got.items[0].main_item.pet
            assert pet.catch_scene_id==1 and pet.catch_area_id==1001 and pet.catch_time and pet.item_id==200001001
            assert hunt(a,16)==got and amount(pid,quantum)==4
            second=hunt(a,17).items[0].main_item.pet
            first,other=pet.instance_id,second.instance_id
            assert other!=first
            page=call(a,"GetPet",20,start_index=0,end_index=1)
            assert page.total_num==2 and page.end_index==1 and list(page.pets)==[pet]
            page=call(a,"GetPet",21,start_index=1,end_index=10)
            assert page.total_num==2 and page.end_index==2 and list(page.pets)==[second]
            assert call(a,"GetPet",22,start_index=4,end_index=3).status==pb.StatusCode_BAD_REQ
            assert not call(b,"GetPet",23,start_index=0,end_index=10).pets
            assert call(b,"ChangePet",24,pet_instance_id=first).status==pb.StatusCode_PET_NOT_FOUND
            assert rename(b,25,pet_instance_id=first,name="错位").status==pb.StatusCode_PET_NOT_FOUND
            assert rename(a,26,pet_instance_id=first,name="103").status==pb.StatusCode_BAD_REQ
            assert rename(a,27,pet_instance_id=first,name="小灰").name=="小灰"
            assert call(a,"ChangePet",28,pet_instance_id=first).status==pb.StatusCode_OK
            assert scene_pet(b,"小灰")==200001001
            assert rename(a,29,pet_instance_id=first,name="云朵").status==pb.StatusCode_OK
            assert scene_pet(b,"云朵")==200001001
            assert release(a,30,other,first).status==pb.StatusCode_BAD_REQ
            assert release(a,31,other,other).status==pb.StatusCode_BAD_REQ
            assert release(a,32,other,999999).status==pb.StatusCode_PET_NOT_FOUND
            assert release(b,33,other).status==pb.StatusCode_PET_NOT_FOUND
            starting={k:amount(pid,k) for k in (200101002,200101003)}
            result=release(a,34,other)
            assert result.status==pb.StatusCode_OK and list(result.pet_instance_ids)==[other]
            yields={i.main_item.item_id:i.main_item.base_item.num for i in result.items}
            assert yields.keys()==starting.keys() and all(1<=n<=4 for n in yields.values())
            assert release(a,34,other)==result and release(a,35,other).status==pb.StatusCode_PET_NOT_FOUND
            assert all(amount(pid,k)==starting[k]+n for k,n in yields.items())
            assert call(a,"ItemUse",36,item_id=bag,num=2).status==pb.StatusCode_OK
            assert call(a,"ItemUse",36,item_id=bag,num=2).status==pb.StatusCode_OK and amount(pid,bag)==0
            assert capacity(pid)==102 and capacity(peer)==100
            # Filled historical warehouse must reject capture before charging; exercise a bulk import.
            with game.lock,db.conn:
                clones=[]
                for n in range(101):
                    extra=make_item(200001001,1,pid)
                    key=extra.main_item.pet.instance_id
                    db.set_item_detail(pid,extra.SerializeToString(),instance_id=key)
                    clones.append(key)
            assert hunt(a,37).status==pb.StatusCode_PET_GRID_FULL and amount(pid,quantum)==3
            with game.lock,db.conn:
                for key in clones:
                    db.del_item_detail(pid,key)
    finally:
        game.stop()
    game.start()
    try:
        a,_,main=enter(0)
        with a:
            assert main.pet_instance_id==first and capacity(pid)==102
            pets=call(a,"GetPet",40).pets
            assert len(pets)==1 and pets[0].instance_id==first and pets[0].name=="云朵"
            assert call(a,"ChangePet",41,pet_instance_id=0).status==pb.StatusCode_OK
            assert release(a,42,first).status==pb.StatusCode_OK
    finally:
        game.stop()
    print("PASS native pet capture, catcher spending/limits, release material ranges, paging, world display, per-role state, retry and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
