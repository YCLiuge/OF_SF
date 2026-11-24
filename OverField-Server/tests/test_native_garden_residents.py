"""Native role seating, furniture removal and garden snapshots for three simultaneous players."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_dungeons import _table
from test_game_realm import join,login,next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile("花园"+str(i)) for i in range(3)]
    service=LocalGameService(identity,root,resources,port=0)
    sequence=30
    def ask(s,n,**fields):
        nonlocal sequence
        sequence+=1
        return call(s,n,sequence,**fields)
    def connect(stack,index):
        sock,_,uid=login(service.port,identity.issue_ticket(*profiles[index]))
        stack.enter_context(sock)
        main,scene=join(sock)
        return sock,uid,main,scene.data
    def garden(s,channel):
        change=ask(s,"ChangeSceneChannel",channel_label=channel,scene_id=9999)
        assert change.status==pb.StatusCode_OK
        return next_msg(s,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    def place(s,cid,fid,seat=0):
        return ask(s,"GardenPlaceCharacter",character_id=cid,furniture_id=fid,seat_id=seat)
    def notice(s):
        return next_msg(s,MsgId.GardenPlaceCharacterNotice,pb.GardenPlaceCharacterNotice)
    def stack_of(uid,i,n):
        db.set_item_detail(uid,make_item(i,n,uid).SerializeToString(),i)
    service.start()
    try:
        with ExitStack() as group:
            a,uid,_,_=connect(group,0)
            b,bid,_,_=connect(group,1)
            c,cid,_,_=connect(group,2)
            assert ask(a,"SwitchGardenStatus",is_open=True).status==pb.StatusCode_OK
            for sock in (a,b):garden(sock,uid)
            garden(c,1)
            char_table=_table("Character","character")
            heroes=[cid for cid,row in char_table.items() if cid!=101001 and row.get("achieve_reward_i_d")][:2]
            h1,h2=heroes
            chair=5042202
            trophy=5040001
            fid=8000001093
            medals=[]
            with service.lock,db.conn:
                for hero in heroes:
                    raw=pb.Character.FromString(db.get_characters(uid,101001)[0])
                    raw.character_id=hero
                    db.set_character(uid,hero,raw.SerializeToString())
                    track=char_table[hero]["achieve_reward_i_d"]
                    medals.append(_table("Character","character_achieve_reward")[track]["achieve_reward_info"][4]["unlock_item_i_d"])
                stack_of(uid,chair,2)
                stack_of(uid,trophy,1)
                for medal in medals:stack_of(uid,medal,4)
            for item,instance in ((chair,fid),(trophy,fid+1)):
                assert ask(a,"PlaceFurniture",furniture_id=instance,furniture_item_id=item).status==pb.StatusCode_OK
            assert place(a,h1,fid).status==pb.StatusCode_GARDEN_PLACE_CHARACTER_CONDITION_NOT_MET
            with service.lock,db.conn:
                for medal in medals:stack_of(uid,medal,5)
            assert place(a,h1,fid+1).status==pb.StatusCode_BAD_REQ
            assert place(a,h1,fid,4).status==pb.StatusCode_BAD_REQ
            assert place(a,101001,fid).status==pb.StatusCode_CHARACTER_IN_TEAM
            assert place(b,h1,fid).status==pb.StatusCode_GARDEN_PLACE_CHARACTER_CONDITION_NOT_MET
            assert place(a,h1,fid).status==pb.StatusCode_OK
            placed=notice(b).character
            assert (placed.character_id,placed.furniture_id,placed.seat_id)==(h1,fid,0)
            assert place(a,h1,fid,1).status==pb.StatusCode_GARDEN_PLACE_CHARACTER_ALREADY
            assert place(a,h2,fid).status==pb.StatusCode_FURNITURE_OCCUPY
            assert ask(b,"SceneSitChair",chair_id=fid,is_sit=True).status==pb.StatusCode_ChairSeatTaken
            assert ask(b,"SceneSitChair",chair_id=fid,seat_id=1,is_sit=True).status==pb.StatusCode_OK
            assert place(a,h2,fid,1).status==pb.StatusCode_FURNITURE_OCCUPY
            assert ask(a,"UpdateTeam",char1=h1).status==pb.StatusCode_CHARACTER_PLACED
            assert ask(a,"UpdateTeam",char1=101001,char2=h1).status==pb.StatusCode_OK
            late=garden(c,uid)
            assert [(x.character_id,x.furniture_id) for x in late.placed_characters]==[(h1,fid)]
            assert [(x.chair_id,x.seat_id,x.player_id) for x in late.chair_info_list]==[(fid,1,bid)]
            garden(a,1)
            # The original client only supplies character_id and is_remove, including from team management.
            assert ask(a,"GardenPlaceCharacter",character_id=h1,is_remove=True).status==pb.StatusCode_OK
            assert notice(c).remove_character_id==h1
            assert ask(b,"SceneSitChair",chair_id=fid,seat_id=1,is_sit=False).status==pb.StatusCode_OK
            garden(a,uid)
            assert place(a,h1,fid,2).status==pb.StatusCode_OK
            assert ask(a,"GardenFurnitureSave",scheme_id=0).status==pb.StatusCode_OK
    finally:
        service.stop()
    service=LocalGameService(identity,root,resources,port=0)
    service.start()
    try:
        with ExitStack() as group:
            a,uid,main,scene=connect(group,0)
            assert list(main.placed_characters)==[h1]
            assert [(x.character_id,x.seat_id) for x in scene.placed_characters]==[(h1,2)]
            b,bid,_,_=connect(group,1)
            assert ask(a,"GardenFurnitureApplyScheme",scheme_id=0).status==pb.StatusCode_OK
            assert [(x.character_id,x.seat_id) for x in garden(b,uid).placed_characters]==[(h1,2)]
            assert ask(a,"TakeOutFurniture",furniture_id=fid,is_put_in_package=True).status==pb.StatusCode_OK
            assert notice(b).remove_character_id==h1
            assert not garden(b,uid).placed_characters
            assert ask(a,"GardenFurnitureApplyScheme",scheme_id=0).status==pb.StatusCode_OK
            assert place(a,h1,fid,3).status==pb.StatusCode_OK
            assert ask(a,"GardenFurnitureRemoveAll").status==pb.StatusCode_OK
            assert not garden(b,uid).placed_characters
    finally:
        service.stop()
    print("PASS owner/visitor native garden snapshots, PC fifth medal threshold, captain, 4-seat item, seat conflicts, character-only recall, layout exchange and restart.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as t:
        run(Path(t),p.parse_args().resources)
