"""Authenticated UID and nickname adoption, conflict handling and public UID routing on native sockets."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import MsgId,pb
from local_identity import NicknameUnavailable
from save_import_data import project
from test_game_realm import join,login,next_msg
from test_native_chat import barrier,texts,DM
from test_native_gacha import call
from test_native_save_import import synthetic,world
from test_private_profiles import request
from utils import db
import realm_uids


def run(root,resources):
    high=4000123456
    raw=synthetic()
    original=raw["decoded"]["main"]["frames"][0]["decoded"]
    internal=7123456
    original.update(player_id=internal,player_label=high,player_name="星庭旧名")
    raw["account"]={"playerId":internal,"playerLabel":high,"playerName":"星庭旧名","uid":"private-channel-id","username":"private-login"}
    data=project(raw)
    assert data["identity"]=={"uid":high,"nickname":"星庭旧名"}
    conflict=deepcopy(raw)
    conflict["account"].update(playerLabel=high-1,playerName="相异")
    assert project(conflict)["identity"]=={"uid":0,"nickname":""}
    old=deepcopy(raw)
    del old["account"]["playerLabel"],old["account"]["playerName"]
    del old["decoded"]["main"]["frames"][0]["decoded"]["player_label"],old["decoded"]["main"]["frames"][0]["decoded"]["player_name"]
    assert "identity" not in project(old)
    password="test-only-import-password"
    def http(action,**values):
        return request(port,"POST","/local/v1/accounts/"+action,values)
    def register(user,nick=None):
        fields={} if nick is None else {"nickname":nick}
        status,account=http("register",username=user,password=password,**fields)
        assert status==201,(status,account.get("error"))
        return account
    def relog(user):
        code,profile=http("login",username=user,password=password)
        assert code==200
        return profile
    def preview(acct,src,legacy_client=False):
        payload=deepcopy(src)
        identity=payload.get("identity",{})
        if "uid" in identity and not legacy_client:
            identity["public_label"]=identity.pop("uid")
        status,info=http("import-preview",import_ticket=acct["import_ticket"],data=payload)
        assert status==200 and info["eligible"]
        return info
    def apply(acct,info,choice=None):
        extra={"identity":choice} if choice is not None else {}
        return http("import-apply",import_ticket=acct["import_ticket"],preview_id=info["preview_id"],**extra)
    def switch(sock,ch,packet,owner=0):
        answer=call(sock,"ChangeSceneChannel",packet,scene_id=9999,channel_label=ch,target_player_label=owner)
        assert (answer.status,answer.channel_id,answer.channel_label)==(pb.StatusCode_OK,pid,ch)
        assert answer.target_player_id==(pid if owner else 0)
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data

    with world(root,resources) as (port,service):
        alpha=register("identity.alpha","本服原名")
        beta=register("identity.beta","备用角色")
        blocked=register("identity.reserved","保留名字")
        assert preview(blocked,data,legacy_client=True)["identity"]["uid_status"]=="missing"
        x=preview(alpha,data);y=preview(beta,data)
        for candidate in (x,y):
            assert candidate["identity"]=={"uid":high,"nickname":"星庭旧名",
                                           "uid_status":"available","nickname_status":"available"}
        # Racing preview holders can never share the original UID or name.
        with ThreadPoolExecutor(2) as pool:
            outcomes=list(pool.map(lambda pair:apply(*pair),((alpha,x),(beta,y))))
        assert sorted(code for code,_ in outcomes)==[200,409],outcomes
        idx=0 if outcomes[0][0]==200 else 1
        winner,loser=(alpha,beta) if idx==0 else (beta,alpha)
        winner_user,loser_user=("identity.alpha","identity.beta") if idx==0 else ("identity.beta","identity.alpha")
        prior_name,new_name=("本服原名","备用角色") if idx==0 else ("备用角色","本服原名")
        winning=(x,y)[idx]
        assert apply(winner,winning)[1]["identity"]=={"uid":high,"nickname":"星庭旧名"}
        lost=preview(loser,data)
        assert lost["identity"]["uid_status"]==lost["identity"]["nickname_status"]=="taken"
        assert apply(loser,lost,{"uid_mode":"new","nickname_mode":"custom","nickname":"保留名字"})[0]==409
        assert apply(loser,lost,{"uid_mode":"new","nickname_mode":"custom","nickname":" "})[0]==400
        assert apply(loser,lost,{"uid_mode":"new","nickname_mode":"custom","nickname":"新名旅人"})[0]==200
        assert http("register",username="identity.reserve.attempt",password=password,nickname=prior_name)[0]==409
        try:
            service.identity.create_profile(prior_name)
        except NicknameUnavailable:
            pass
        else:
            raise AssertionError("A legacy profile claimed the reserved original name")
        # No membership or grants moved from another slot. No hidden low ID resolves to the imported player.
        pid=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(winner["slot"],)).fetchone()[0]
        assert pid!=high and realm_uids.local_uid(high)==pid and realm_uids.local_uid(pid)==realm_uids.INVALID
        assert not db.conn.execute("SELECT 1 FROM gm_grants WHERE slot=?",(winner["slot"],)).fetchone()
        invalid=deepcopy(data);invalid["identity"]={"uid":1,"nickname":""}
        missing=preview(blocked,invalid)["identity"]
        assert missing["uid_status"]==missing["nickname_status"]=="invalid"
        assert preview(blocked,project(old))["identity"]=={"uid":None,"nickname":"","uid_status":"missing","nickname_status":"missing"}
        un=http("import-undo",import_ticket=winner["import_ticket"])
        assert un[0]==200 and un[1]["identity"]=={"uid":pid,"nickname":prior_name}
        assert realm_uids.local_uid(pid)==pid and not db.conn.execute("SELECT 1 FROM realm_public_uids WHERE uid=?",(high,)).fetchone()
        assert relog(winner_user)["nickname"]==prior_name
        again=preview(winner,data)
        assert apply(winner,again)[0]==200
        pending=register("identity.pending")
        keep=preview(pending,data)
        assert apply(pending,keep,{"uid_mode":"new","nickname_mode":"local"})[1]["identity"]["nickname"]==""
        assert relog("identity.pending")["nickname"]==""
        assert http("import-undo",import_ticket=pending["import_ticket"])[1]["identity"]["nickname"]==""
        with service.lock,db.conn:
            legacy=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(pending["slot"],)).fetchone()[0]
            previous=realm_uids.next_uid(db.conn)
            db.conn.execute("INSERT INTO realm_public_uids VALUES(?,?)",(legacy,previous))
        realm_uids.refresh(db.conn)
        another=deepcopy(data)
        another["identity"]={"uid":high-1}
        fresh=preview(pending,another)
        assert apply(pending,fresh)[1]["identity"]=={"uid":high-1,"nickname":""}
        another["identity"]={"uid":previous,"nickname":"\x01"}
        earlier=preview(blocked,another)["identity"]
        assert earlier["uid_status"]=="taken" and earlier["nickname_status"]=="invalid" and not earlier["nickname"]
        assert realm_uids.next_uid(db.conn)>previous
        assert http("import-undo",import_ticket=pending["import_ticket"])[1]["identity"]=={"uid":previous,"nickname":""}
        assert realm_uids.local_uid(previous)==legacy and realm_uids.next_uid(db.conn)>previous
        restored=realm_uids.outgoing(pb.PlayerMainDataRsp(player_id=legacy,player_label=legacy,channel_id=previous))
        assert (restored.player_id,restored.player_label,restored.channel_id)==(previous,previous,previous)
        assert realm_uids.from_wire_uid(previous)==legacy
        inventory=pb.ItemDetail()
        assert realm_uids.outgoing(inventory) is inventory
        raw_args=pb.PlayerMainDataReq().SerializeToString()
        assert realm_uids.incoming(MsgId.PlayerMainDataReq,raw_args) is raw_args
    with world(root,resources) as (port,service):
        account=relog(winner_user)
        s,info,uid=login(service.port,account["ticket"])
        assert uid==pid and info.player_name=="星庭旧名"
        partner=relog(loser_user)
        peer,_,ordinary=login(service.port,partner["ticket"])
        assert 1000001<=ordinary<1000020
        with closing(s),closing(peer):
            main,scene=join(s)
            assert (main.player_id,main.player_label,main.player_name)==(pid,high,"星庭旧名")
            join(peer)
            search=call(peer,"FriendSearch",66,search_args=str(high))
            assert search.status==pb.StatusCode_OK and search.data.player_id==pid
            assert call(peer,"FriendSearch",67,search_args=str(ordinary)).data.player_id==ordinary
            assert call(peer,"FriendSearch",68,search_args=str(pid)).status!=pb.StatusCode_OK
            with service.lock,db.conn:
                db.set_players_info(pid,"birthday","2001-03-14")
            profile=call(peer,"OtherPlayerInfo",70,player_id=search.data.player_id).other_info
            assert (profile.player_id,profile.player_label,profile.birthday)==(pid,high,"2001-03-14")
            assert call(peer,"OtherPlayerInfo",69,player_id=high).status!=pb.StatusCode_OK
            home=switch(s,high,71)
            assert (home.channel_id,home.channel_label)==(pid,high)
            visited=switch(peer,high,72,owner=high)
            assert (visited.channel_id,visited.channel_label)==(pid,high) and any(p.player_id==pid for p in visited.players)
            assert call(s,"SendChatMsg",73,type=DM,player_id=ordinary,text="归档问候").status==pb.StatusCode_OK
            assert (DM,pid,"归档问候") in texts(barrier(peer))
            assert http("import-undo",import_ticket=account["import_ticket"])[0]==409
            assert http("register",username="identity.released",password=password,nickname=prior_name)[0]==201
        assert not db.conn.execute("SELECT 1 FROM private_import_identity WHERE slot=?",(account["slot"],)).fetchone()
        assert not db.conn.execute("SELECT 1 FROM authority.realm_import_name_reservations WHERE slot=?",(account["slot"],)).fetchone()
    with world(root,resources) as (port,service):
        owner,entry,again=login(service.port,relog(winner_user)["ticket"])
        with closing(owner):
            main,home=join(owner)
            assert (again,entry.channel_id,main.player_id,main.channel_id,home.data.channel_id)==(pid,)*5
            assert (main.player_label,main.channel_label,home.data.channel_label)==(high,)*3
    print("PASS role label and native ID split, legacy aliases, competing imports, undo/restart, native login/role/home/search/chat/profile.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as folder:
        run(Path(folder),parser.parse_args().resources)
