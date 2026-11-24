"""Scene equipment stays current while the original owner changes items through normal TCP requests."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_dungeons import _table
from private_equipment import load, property_group
from private_inventory import compact, replace_slots
from test_game_realm import join_after_intro, login, next_msg
from test_native_equipment_presets import make
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    users=LocalIdentity(root/"identity.sqlite3")
    realm=LocalGameService(users,root,resources,port=0)
    seq=count(100)
    def act(sock,name,**args):
        reply=call(sock,name,next(seq),**args)
        assert reply.status==pb.StatusCode_OK, (name,reply)
        return reply
    def updates(sock,player):
        # The inventory response fences pending scene notifications on the same TCP stream.
        packet=next(seq)
        sock.sendall(encode(MsgId.GetWeaponReq,pb.GetWeaponReq().SerializeToString(),packet))
        found=[]
        for _ in range(256):
            head,data=receive(sock)
            if head.msg_id==MsgId.GetWeaponRsp and head.packet_id==packet:
                return found
            if head.msg_id==MsgId.ServerSceneSyncDataNotice:
                notice=pb.ServerSceneSyncDataNotice.FromString(data)
                found.extend(a.player.team for record in notice.data if record.player_id==player
                             for a in record.server_data if a.action_type==pb.SceneActionType_UPDATE_EQUIP)
        raise AssertionError("missing inventory fence")
    realm.start()
    cid,second=101001,101002
    try:
        with ExitStack() as clients:
            opened=[]
            for name in ("场景装备甲","同频道观察","其他频道","其他场景"):
                role=users.create_profile(name)
                sock,_,pid=login(realm.port,users.issue_ticket(*role))
                clients.enter_context(sock)
                join_after_intro(sock)
                opened.append((sock,pid))
            (owner,pid),(peer,_),(other_channel,_),(other_scene,_)=opened
            act(other_channel,"ChangeSceneChannel",scene_id=9999,channel_label=2)
            act(other_scene,"ChangeSceneChannel",scene_id=1,channel_label=1)
            with realm.lock,db.conn:
                db.add_character(pid,second)
                poster=make(pid,10010,"poster",star=1)
                fodder=make(pid,10010,"poster",star=1)
                spare=make(pid,1101104,"weapon",star=2)
                blade=make_item(1101104,1,pid)
                blade.main_item.weapon.star=3
                helmet=make_item(2111001,1,pid)
                sword,head=blade.main_item.weapon.instance_id,helmet.main_item.armor.instance_id
                replace_slots(pid,compact([blade,helmet]))
                for kind,part in (("weapon",blade.main_item.weapon),("armor",helmet.main_item.armor)):
                    cfg=property_group(kind,part)
                    entries=_table(kind.title(),kind+"_strengthen")[cfg["strengthen_i_d"]][kind+"_strengthen_group_info"]
                    costs=next(row for row in entries if row["level"]==1)[kind+"_strength_need_item"]
                    for row in costs:
                        tid=row["need_item_i_d"]
                        db.set_item_detail(pid,make_item(tid,10000,pid).SerializeToString(),item_id=tid)
                for tid in (101,205):
                    db.set_item_detail(pid,make_item(tid,10000,pid).SerializeToString(),item_id=tid)
                rune=make_item(20001,1,pid)
                rune.main_item.inscription.level=2
                db.set_item_detail(pid,rune.SerializeToString(),item_id=20001)
            act(owner,"UpdateTeam",char1=cid,char2=second)
            record=pb.PlayerSceneRecordReq()
            step=record.data.char_recorder_data_lst.add(char_id=cid)
            step.pos.x,step.pos.y,step.pos.z,step.rot.y=272,318,-317,66
            owner.sendall(encode(MsgId.PlayerSceneRecordReq,record.SerializeToString(),next(seq)))
            next_msg(peer,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            observed_second_pose=[]
            def changed():
                reports=updates(peer,pid)
                assert len(reports)==1,len(reports)
                team=reports[0]
                lead=team.char1
                assert (lead.pos.x,lead.pos.y,lead.pos.z,lead.rot.y)==(272,318,-317,66)
                pose=(team.char2.pos.SerializeToString(),team.char2.rot.SerializeToString())
                if not observed_second_pose:
                    observed_second_pose.append(pose)
                assert pose==observed_second_pose[0]
                return team
            act(owner,"CharacterEquipUpdate",char_id=second,equipment_preset=pb.EquipmentPreset(weapon=spare))
            changed()
            act(owner,"CharacterEquipUpdate",char_id=cid,equipment_preset=pb.EquipmentPreset(posters=[pb.PosterInfo(poster_id=poster)]))
            assert changed().char1.posters[0].poster_star==1
            act(owner,"PosterStarUp",poster_instance_id=poster,cost_poster_instance_id=[fodder])
            assert changed().char1.posters[0].poster_star==2
            act(owner,"TempPackWearEquip",char_id=cid,index=1)
            worn=changed().char1
            assert (worn.weapon_id,worn.weapon_star)==(1101104,3)
            act(owner,"TempPackWearEquip",char_id=cid,index=1)
            assert [(a.armor_id,a.armor_star) for a in changed().char1.armors]==[(2111001,0)]
            assert act(owner,"ArmorStrength",armor_instance_id=head).strength_level==1
            assert changed().char1.armors[0].armor_star==1
            assert act(owner,"WeaponStrength",weapon_instance_id=sword).strength_level==1
            assert load(pid,sword,"weapon").main_item.weapon.strength_level==1
            # No weapon strength-level field exists in SceneCharacter. Its separate
            # weapon_star value and the owner's actual inventory level stay intact.
            assert not updates(peer,pid)
            act(owner,"WeaponInscriptionInlaid",weapon_instance_id=sword,inscription_id=20001)
            before=changed()
            assert (before.char1.inscription_id,before.char1.inscription_lv,before.char1.weapon_star)==(20001,2,3)
            act(owner,"WeaponInscriptionInlaid",weapon_instance_id=spare,inscription_id=20001)
            moved=changed()
            assert (moved.char1.inscription_id,moved.char2.inscription_id,moved.char2.inscription_lv)==(0,20001,2)
            act(owner,"WeaponInscriptionRemove",weapon_instance_id=spare)
            assert changed().char2.inscription_id==0
            assert not updates(other_channel,pid) and not updates(other_scene,pid)
    finally:
        realm.stop()
    print("PASS native poster star, bag weapon/armor, equipped strength, transferred inscription, unchanged poses and scene/channel boundaries.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
