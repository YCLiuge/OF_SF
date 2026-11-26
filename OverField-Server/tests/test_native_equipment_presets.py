"""Exercise normal native loadout edits, borrowing and switching through two accounts on a disposable realm."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from private_dungeons import _table
from private_equipment import load
from test_game_realm import join_after_intro, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def current(pid, cid):
    return pb.Character.FromString(db.get_characters(pid,cid)[0])


def wearer(pid, key, kind):
    return getattr(load(pid,key,kind).main_item,kind).wearer_id


def switch(sock, seq, cid, idx):
    return call(sock,"CharacterEquipPresetSwitch",seq,char_id=cid,preset_index=idx)


def update(sock, seq, cid, preset):
    return call(sock,"CharacterEquipUpdate",seq,char_id=cid,equipment_preset=preset)


def make(pid, template, kind, **values):
    item = make_item(template,1,pid)
    instance = getattr(item.main_item,kind)
    for field, value in values.items():
        setattr(instance,field,value)
    db.set_item_detail(pid,item.SerializeToString(),instance_id=instance.instance_id)
    return instance.instance_id


def peer_update(sock, pid):
    for _ in range(64):
        notice = next_msg(sock,MsgId.ServerSceneSyncDataNotice,pb.ServerSceneSyncDataNotice)
        for player in notice.data:
            for action in player.server_data:
                if player.player_id == pid and action.action_type == pb.SceneActionType_UPDATE_EQUIP:
                    return action.player.team
    raise AssertionError("missing peer equipment change")


def run(root,resources):
    users = LocalIdentity(root/"identity.sqlite3")
    accounts = [users.create_profile("预案甲"),users.create_profile("预案乙")]
    host = LocalGameService(users,root,resources,port=0)
    first,second = 101001,101002
    def enter(index):
        sock,_,pid = login(host.port,users.issue_ticket(*accounts[index]))
        main,scene = join_after_intro(sock)
        return sock,pid,main,scene
    host.start()
    try:
        a,pid,_,_ = enter(0)
        b,peer,_,_ = enter(1)
        with a,b:
            with host.lock,db.conn:
                peer_equipment = current(peer,second).SerializeToString()
                initial = current(pid,first).equipment_presets[0].weapon
                other_initial = current(pid,second).equipment_presets[0].weapon
                head = make(pid,2111001,"armor",strength_level=3)
                card = make(pid,10010,"poster",star=4)
                blade = make(pid,1101104,"weapon",star=3,inscription1=20001)
                rune = make_item(20001,1,pid)
                rune.main_item.inscription.level = 2
                rune.main_item.inscription.weapon_instance_id = blade
                db.set_item_detail(pid,rune.SerializeToString(),item_id=20001)
                wrong_weapon = next(r["default_weapon_i_d"] for r in _table("Character","character").values()
                                    if r.get("default_weapon_i_d") and r.get("new_weapon_system_type") not in (None,1))
                wrong = make(pid,wrong_weapon,"weapon")
            assert call(a,"UpdateTeam",4,char1=first,char2=second).status == pb.StatusCode_OK
            movement = pb.PlayerSceneRecordReq()
            rec = movement.data.char_recorder_data_lst.add(char_id=first)
            rec.pos.x,rec.pos.y,rec.pos.z,rec.rot.y = 127,215,-647,237
            a.sendall(encode(MsgId.PlayerSceneRecordReq,movement.SerializeToString(),5))
            next_msg(b,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            dest = pb.EquipmentPreset(preset_index=1,weapon=blade)
            dest.armors.add(equip_type=0,armor_id=head)
            dest.posters.add(poster_index=2,poster_id=card)

            # Saving an inactive preset must not take the currently worn items.
            saved = update(a,6,second,dest)
            assert saved.status == pb.StatusCode_OK and not saved.items
            canonical = saved.character[0].equipment_presets[1]
            assert [x.equip_type for x in canonical.armors]==list(range(4))
            assert [x.poster_index for x in canonical.posters]==list(range(3))
            assert wearer(pid,blade,"weapon")==wearer(pid,card,"poster")==0
            assert current(pid,second).in_use_equipment_preset_index==0
            before = current(pid,second).SerializeToString()
            assert switch(a,7,second,3).status==pb.StatusCode_BAD_REQ
            assert switch(a,8,55555555,1).status==pb.StatusCode_BAD_REQ
            assert call(a,"CharacterEquipUpdate",9,char_id=second).status==pb.StatusCode_BAD_REQ
            for seq,preset in enumerate((
                pb.EquipmentPreset(preset_index=3),
                pb.EquipmentPreset(weapon=wrong),
                pb.EquipmentPreset(weapon=card),
                pb.EquipmentPreset(armors=[pb.ArmorInfo(equip_type=1,armor_id=head)]),
                pb.EquipmentPreset(armors=[pb.ArmorInfo(equip_type=1),pb.ArmorInfo(equip_type=1)]),
                pb.EquipmentPreset(posters=[pb.PosterInfo(poster_index=3,poster_id=card)]),
                pb.EquipmentPreset(posters=[pb.PosterInfo(poster_id=card),pb.PosterInfo(poster_index=1,poster_id=card)]),
                pb.EquipmentPreset(weapon=blade,posters=[pb.PosterInfo(poster_id=999999999)]),
            ),10):
                assert update(a,seq,second,preset).status==pb.StatusCode_BAD_REQ
            assert update(b,18,first,dest).status==pb.StatusCode_BAD_REQ
            assert current(pid,second).SerializeToString()==before and wearer(pid,blade,"weapon")==0

            applied = switch(a,19,second,1)
            assert applied.status==pb.StatusCode_OK and applied.preset_index==1
            assert applied==switch(a,19,second,1)
            for kind,key in (("weapon",blade),("armor",head),("poster",card)):
                assert wearer(pid,key,kind)==second
            assert wearer(pid,other_initial,"weapon")==0
            team = peer_update(b,pid)
            assert (team.char1.pos.x,team.char1.pos.y,team.char1.pos.z,team.char1.rot.y)==(127,215,-647,237)
            equipped = team.char2
            assert (equipped.weapon_id,equipped.weapon_star,equipped.inscription_id,equipped.inscription_lv)==(1101104,3,20001,2)
            assert [(x.armor_id,x.armor_star) for x in equipped.armors]==[(2111001,3)]
            assert [(x.poster_id,x.poster_star) for x in equipped.posters]==[(10010,4)]
            assert current(pid,first).equipment_presets[0].weapon==initial

            # Borrow into an active preset. The displaced wearer's preset keeps its slot layout.
            direct = pb.EquipmentPreset(weapon=blade)
            direct.posters.add(poster_index=1,poster_id=card)
            response = update(a,20,first,direct)
            assert response.status==pb.StatusCode_OK and response==update(a,20,first,direct)
            assert {x.character_id for x in response.character}=={first,second}
            assert wearer(pid,initial,"weapon")==0 and wearer(pid,card,"poster")==first
            previous = current(pid,second).equipment_presets[1]
            assert not previous.weapon and len(previous.posters)==3 and not any(p.poster_id for p in previous.posters)
            assert previous.armors[0].armor_id==head and wearer(pid,head,"armor")==second
            team = peer_update(b,pid)
            assert team.char1.weapon_star==3 and team.char1.posters[0].poster_id==10010
            assert not team.char2.weapon_id and not team.char2.posters and team.char2.armors[0].armor_id==2111001
            assert team.char1.pos.z==-647 and team.char1.rot.y==237
            moved = pb.EquipmentPreset(preset_index=1,weapon=blade)
            moved.posters.add(poster_index=0,poster_id=card)
            assert update(a,21,first,moved).status==pb.StatusCode_OK
            result = switch(a,22,first,1)
            assert result.status==pb.StatusCode_OK and wearer(pid,card,"poster")==first
            empty = switch(a,23,first,2)
            assert empty.status==pb.StatusCode_OK and wearer(pid,card,"poster")==wearer(pid,blade,"weapon")==0
            assert all(not getattr(i.main_item,i.main_item.WhichOneof("item")).wearer_id for i in empty.items)
            assert switch(a,24,first,0).status==pb.StatusCode_OK
            # A saved inactive plan can borrow on activation as well.
            dest.preset_index=2
            assert update(a,25,second,dest).status==pb.StatusCode_OK and wearer(pid,blade,"weapon")==first
            changed = switch(a,26,second,2)
            assert changed.status==pb.StatusCode_OK and changed==switch(a,26,second,2)
            assert wearer(pid,card,"poster")==wearer(pid,blade,"weapon")==second
            assert all(not p.weapon and not any(s.poster_id for s in p.posters) for p in current(pid,first).equipment_presets)
            assert current(peer,first).equipment_presets[0].weapon and current(peer,second).SerializeToString() == peer_equipment
    finally:
        host.stop()
    host.start()
    try:
        a,again,main,notice = enter(0)
        with a:
            assert again==pid
            chars = {c.character_id:c for c in main.characters}
            assert chars[second].in_use_equipment_preset_index==2 and not chars[first].equipment_presets[0].weapon
            team = next(p.team for p in notice.data.players if p.player_id==pid)
            assert team.char2.char_id==second and team.char2.posters[0].poster_id==10010
            assert team.char2.inscription_id==20001 and team.char2.armors[0].armor_star==3
            assert (team.char1.pos.x,team.char1.pos.y,team.char1.pos.z,team.char1.rot.y)==(127,215,-647,237)
            assert switch(a,30,second,2).status==pb.StatusCode_OK
            # Pre-fix saves could give an off-team character an active item without its wearer flag.
            with host.lock,db.conn:
                third = db.add_character(pid,101003)
                old_weapon = third.equipment_presets[0].weapon
                orphan = make(pid,1101104,"weapon")
                third.equipment_presets[0].weapon = orphan
                db.set_character(pid,101003,third.SerializeToString())
                obsolete = load(pid,old_weapon,"weapon")
                obsolete.main_item.weapon.wearer_id = 0
                db.set_item_detail(pid,obsolete.SerializeToString(),instance_id=old_weapon)
            repaired = update(a,31,first,pb.EquipmentPreset(weapon=orphan))
            assert repaired.status==pb.StatusCode_OK and wearer(pid,orphan,"weapon")==first
            assert {char.character_id for char in repaired.character}=={first,101003}
            assert not current(pid,101003).equipment_presets[0].weapon
            parked = update(a,32,101003,pb.EquipmentPreset(weapon=old_weapon))
            assert parked.status==pb.StatusCode_OK and wearer(pid,old_weapon,"weapon")==101003

    finally:
        host.stop()
    print("PASS three native loadouts, ownership, cross-role borrows, slot compatibility, peer equipment, persistent position, replay and restart.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
