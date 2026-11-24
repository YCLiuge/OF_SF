"""Native ten-slot appearances, personal notification, identified peer view and storage."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService,MsgId,pb
from local_identity import LocalIdentity
from private_character_appearance import STARTER,TOOLS
from private_dungeons import progress
from realm_uids import public_uid
from test_game_realm import login,join_after_intro
from test_native_events import NOTICES,rpc
from test_native_gacha_atomicity import set_balance
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    roles=LocalIdentity(root/"identity.sqlite3")
    profiles=[roles.create_profile(n) for n in ("外观队长","本频道玩家","外频道玩家")]
    server=LocalGameService(roles,root,resources,port=0)
    seq=count(201)
    hero,bench=101001,101002
    NOTICES.update({getattr(MsgId,n):getattr(pb,n) for n in ("CharacterAppearanceUpdateNotice","ServerSceneSyncDataNotice")})
    def ask(s,name,**values):
        return rpc(s,name,next(seq),**values)
    def apply(s,a,x=hero):
        return ask(s,"UpdateCharacterAppearance",char_id=x,appearance=a)
    def views(frames):
        return [(rec.player_id,act.player) for m in frames if isinstance(m,pb.ServerSceneSyncDataNotice)
                for rec in m.data for act in rec.server_data if act.action_type==pb.SceneActionType_UPDATE_APPEARANCE]
    def private(frames):
        return [m.appearance for m in frames if isinstance(m,pb.CharacterAppearanceUpdateNotice)]
    def item(pid,template):
        detail=make_item(template,1,pid)
        key=detail.main_item.weapon.instance_id
        db.set_item_detail(pid,detail.SerializeToString(),instance_id=key)
        return key
    server.start()
    try:
        with ExitStack() as sockets:
            players=[]
            for profile in profiles:
                s,_,pid=login(server.port,roles.issue_ticket(*profile))
                sockets.enter_context(s)
                join_after_intro(s)
                players.append((s,pid))
            (owner,pid),(peer,other),(third,_)=players
            assert ask(third,"ChangeSceneChannel",scene_id=9999,channel_label=2)[0].status==pb.StatusCode_OK
            with server.lock,db.conn:
                db.add_character(pid,bench)
                old=pb.Character.FromString(db.get_characters(pid,hero)[0]).character_appearance
                assert all(getattr(old,k)==v for k,v in STARTER.items())
                for template in (5001,4000):set_balance(pid,template,1)
                set_balance(other,4001,1)
                actual=dict(zip(TOOLS,(1004002,1002002,1005002,1003002,1001002,1006002,1009001,1007002)))
                gear={slot:item(pid,tmp) for slot,tmp in actual.items()}
                other_shovel=90000032
                foreign=make_item(1007002,1,other)
                foreign.main_item.weapon.instance_id=other_shovel
                db.set_item_detail(other,foreign.SerializeToString(),instance_id=other_shovel)
                assert not db.get_item_detail(pid,instance_id=other_shovel)
            expected=pb.CharacterAppearance(badge=5001,umbrella_id=4000,**gear)
            assert ask(owner,"UpdateCharacterAppearance",char_id=hero)[0].status==pb.StatusCode_BAD_REQ
            assert apply(owner,expected,999999)[0].status==pb.StatusCode_CharNotExist
            assert apply(owner,pb.CharacterAppearance(umbrella_id=4001))[0].status==pb.StatusCode_BAD_REQ
            assert apply(owner,pb.CharacterAppearance(badge=4000))[0].status==pb.StatusCode_BAD_REQ
            assert apply(owner,pb.CharacterAppearance(mining_hammer_instance_id=gear["fishing_rod_instance_id"]))[0].status==pb.StatusCode_BAD_REQ
            assert apply(owner,pb.CharacterAppearance(shovel_instance_id=1007002))[0].status==pb.StatusCode_BAD_REQ
            assert apply(owner,pb.CharacterAppearance(shovel_instance_id=other_shovel))[0].status==pb.StatusCode_BAD_REQ
            key=next(seq)
            change,frames=rpc(owner,"UpdateCharacterAppearance",key,char_id=hero,appearance=expected)
            assert change.status==pb.StatusCode_OK and change.char_id==hero and change.appearance==expected and private(frames)==[expected]
            duplicate,frames=rpc(owner,"UpdateCharacterAppearance",key,char_id=hero,appearance=expected)
            assert duplicate==change and not private(frames)
            packets=ask(peer,"GetWeapon")[1]
            scene=views(packets)
            assert len(scene)==1 and scene[0][0]==public_uid(pid) and scene[0][1].team.char1.character_appearance==expected and not private(packets)
            packets=ask(third,"GetWeapon")[1]
            assert not views(packets) and not private(packets)
            with server.lock:
                assert db.get_players_info(pid,"team_leader_badge")[0]==5001
                assert progress(pid,80100209)==1 and progress(pid,80100310)==1
            cleared=pb.CharacterAppearance()
            reply,frames=apply(owner,cleared)
            assert reply.appearance==cleared and private(frames)==[cleared]
            assert apply(owner,old)[0].appearance==old
            assert apply(owner,expected)[0].status==pb.StatusCode_OK
            ask(peer,"GetWeapon")
            assert apply(owner,pb.CharacterAppearance(badge=5001),bench)[0].status==pb.StatusCode_OK
            packets=ask(peer,"GetWeapon")[1]
            assert not private(packets) and not views(packets)
            assert ask(peer,"DungeonEnter",dungeon_id=11101,char1=hero)[0].status==pb.StatusCode_OK
            assert apply(owner,old)[0].status==pb.StatusCode_OK
            packets=ask(peer,"GetWeapon")[1]
            assert not private(packets) and not views(packets)
            assert apply(owner,expected)[0].status==pb.StatusCode_OK
            with server.lock:
                assert pb.Character.FromString(db.get_characters(other,hero)[0]).character_appearance==old
    finally:
        server.stop()
    server.start()
    try:
        s,_,pid=login(server.port,roles.issue_ticket(*profiles[0]))
        with s:
            _,scene=join_after_intro(s)
            own=next(p for p in scene.data.players if p.player_id==public_uid(pid))
            assert own.team.char1.character_appearance==expected
            assert ask(s,"UpdateTeam",char1=bench)[0].status==pb.StatusCode_OK
            with server.lock: assert db.get_players_info(pid,"team_leader_badge")[0]==5001
    finally:
        server.stop()
    print("PASS 10 native appearance slots, eight owned tool instances, personal 1674, identity-scoped peers, starter migration, empty slots and restart.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
