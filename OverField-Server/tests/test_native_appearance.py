"""Wear, switch, hide and persist owned cosmetics through native two-player TCP sessions."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode
from local_identity import LocalIdentity
from test_game_realm import join,login,next_msg
from test_native_events import rpc, NOTICES
from test_observed_initialization import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("时装甲","时装乙")]
    game=LocalGameService(identity,root,resources,port=0)
    def connect(index):
        sock,_,pid=login(game.port,identity.issue_ticket(*profiles[index]))
        main,_=join(sock)
        return sock,pid,main
    game.start()
    try:
        a,owner,initial=connect(0)
        b,other,_=connect(1)
        with a,b:
            original=next(c for c in initial.characters if c.character_id==101001)
            move=pb.PlayerSceneRecordReq()
            record=move.data.char_recorder_data_lst.add(char_id=101001)
            record.pos.CopyFrom(pb.Vector3(x=9143,y=810,z=-14732))
            record.rot.CopyFrom(pb.Vector3(y=3902))
            a.sendall(encode(MsgId.PlayerSceneRecordReq,move.SerializeToString(),15))
            next_msg(a,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            preset=pb.OutfitPreset()
            preset.CopyFrom(original.outfit_presets[1])
            preset.clothes=4011023
            preset.pend_top,preset.pend_chest,preset.pend_pelvis=4200744,4012014,4200634
            preset.pend_up_face,preset.pend_down_face=4011044,4200045
            preset.pend_left_foot,preset.pend_right_foot=4200036,4200037
            saved=call(a,"OutfitPresetUpdate",char_id=101001,preset=preset)
            assert saved.status==pb.StatusCode_OK and saved.char_id==101001 and saved.preset==preset
            assert call(a,"OutfitPresetSwitch",char_id=101001,use_preset_index=1).status==pb.StatusCode_OK
            hide=pb.OutfitHideInfo(hide_orn=True,hide_braid=True)
            assert call(a,"OutfitHideSwitch",char_id=101001,hide_info=hide).status==pb.StatusCode_OK
            # A peer receives the equipped template, selected dye and current hide switches.
            for _ in range(8):
                sync=next_msg(b,MsgId.ServerSceneSyncDataNotice,pb.ServerSceneSyncDataNotice)
                scene=[rec.player.team.char1 for p in sync.data if p.player_id==owner
                       for rec in p.server_data if rec.action_type==pb.SceneActionType_UPDATE_FASHION]
                assert all(char.pos==record.pos and char.rot==record.rot for char in scene)
                if any(char.outfit_preset.clothes==4011023 and char.outfit_preset.hide_info==hide
                       and char.outfit_preset.pend_right_foot==4200037 for char in scene):break
            else:raise AssertionError("Other player did not see equipped clothes and switches")
            for bad in (dict(clothes=500),dict(preset_index=99),dict(hair=4011023),
                        dict(hair=4011022),dict(clothes_dye_scheme_index=99)):
                invalid=pb.OutfitPreset()
                invalid.CopyFrom(preset)
                for k,v in bad.items():setattr(invalid,k,v)
                assert call(a,"OutfitPresetUpdate",char_id=101001,preset=invalid).status!=pb.StatusCode_OK,bad
            assert call(a,"OutfitPresetSwitch",char_id=0,use_preset_index=0).status!=pb.StatusCode_OK
            assert call(a,"OutfitPresetSwitch",char_id=101001,use_preset_index=20).status!=pb.StatusCode_OK
            assert call(a,"ChangeSign",sign="在罗塔花园等你").sign=="在罗塔花园等你"
            assert call(a,"ChangeSign",sign="甲"*61).status!=pb.StatusCode_OK
            assert 41101 in call(a,"UnlockHeadList").heads
            assert call(a,"ChangeHead",head=41101).status==pb.StatusCode_OK
            assert call(a,"ChangeHead",head=0xffffffff).status!=pb.StatusCode_OK
            assert call(a,"ChangePhoneBackground",phone_background=8001).status!=pb.StatusCode_OK
            assert call(a,"ChangePhoneCase",phone_case=7001).status!=pb.StatusCode_OK
            with game.lock,db.conn:
                for key in (7001,8001,7201,7101):
                    d=make_item(key,1,owner)
                    db.set_item_detail(owner,d.SerializeToString(),key)
            assert call(a,"ChangePhoneBackground",phone_background=8001).status==pb.StatusCode_OK
            assert call(a,"ChangePhoneCase",phone_case=7001).status==pb.StatusCode_OK
            app=pb.PlayerAppearance(avatar_frame=7201,pendant=7101)
            assert call(a,"UpdatePlayerAppearance",appearance=app).appearance==app
            assert call(b,"UpdatePlayerAppearance",appearance=app).status!=pb.StatusCode_OK
            assert call(b,"ChangePhoneCase",phone_case=7001).status!=pb.StatusCode_OK
            # Both players visit the same solo dungeon template from the same channel.
            # A's phone update must reach its owner only, before the success reply.
            NOTICES[MsgId.ServerSceneSyncDataNotice] = pb.ServerSceneSyncDataNotice
            for sock in (a,b):
                assert call(sock,"DungeonEnter",dungeon_id=11101,char1=101001).status==pb.StatusCode_OK
            rpc(b,"PlayerPing",30)
            reply, frames = rpc(a,"ChangePhoneCase",31,phone_case=7001)
            def saw_phone_update(frames):
                return any(isinstance(frame,pb.ServerSceneSyncDataNotice) and
                           any(action.action_type==pb.SceneActionType_UPDATE_PHONE_CASE
                               for entry in frame.data for action in entry.server_data) for frame in frames)
            assert reply.status==pb.StatusCode_OK and saw_phone_update(frames)
            assert not saw_phone_update(rpc(b,"PlayerPing",32)[1])
    finally:game.stop()
    game.start()
    try:
        a,_,main=connect(0)
        b,_,untouched=connect(1)
        with a,b:
            char=next(c for c in main.characters if c.character_id==101001)
            worn=char.outfit_presets[char.in_use_outfit_preset_index]
            assert worn.clothes==4011023 and worn.outfit_hide_info==hide
            assert (worn.pend_top,worn.pend_chest,worn.pend_pelvis,worn.pend_up_face,
                    worn.pend_down_face,worn.pend_left_foot,worn.pend_right_foot)==(
                        4200744,4012014,4200634,4011044,4200045,4200036,4200037)
            assert (main.sign,main.phone_background,main.phone_case)==("在罗塔花园等你",8001,7001)
            assert main.appearance==app
            assert untouched.sign=="" and not untouched.appearance.avatar_frame and untouched.phone_case==0
            assert next(c for c in untouched.characters if c.character_id==101001).in_use_outfit_preset_index==0
    finally:game.stop()
    print("PASS native outfit equip/switch/hide, scene broadcast, ownership and configuration checks, private profile settings and relog.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp),parser.parse_args().resources)
