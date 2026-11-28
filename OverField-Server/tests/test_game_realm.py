"""Run the private realm through real TCP frames, a persistent SQLite backend and issued realm tickets."""
import argparse
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
import time

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive, decode_snappy, MAX_BODY
import struct
from local_identity import LocalIdentity


def login(port,ticket,*,resource_version=""):
    c=socket.create_connection(("127.0.0.1",port),timeout=10)
    c.settimeout(15)
    req=pb.VerifyLoginTokenReq(login_token=ticket)
    c.sendall(encode(MsgId.VerifyLoginTokenReq,req.SerializeToString(),1))
    h,b=receive(c)
    assert h.msg_id==MsgId.VerifyLoginTokenRsp and h.packet_id==1
    auth=pb.VerifyLoginTokenRsp.FromString(b)
    if auth.status!=pb.StatusCode_OK:
        c.close();return None
    c.sendall(encode(MsgId.PlayerLoginReq,pb.PlayerLoginReq(resource_version=resource_version).SerializeToString(),2))
    h,b=receive(c);assert h.msg_id==MsgId.PlayerLoginRsp and h.packet_id==2
    info=pb.PlayerLoginRsp.FromString(b)
    assert info.status==pb.StatusCode_OK and info.player_name.strip() and info.region_name=="private"
    return c,info,auth.user_id


def next_msg(c,cmd,kind):
    for _ in range(160):
        head,body=receive(c)
        if head.msg_id==cmd:return kind.FromString(body)
    raise AssertionError(cmd)


def join(c):
    c.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
    main=next_msg(c,MsgId.PlayerMainDataRsp,pb.PlayerMainDataRsp)
    scene=next_msg(c,MsgId.SceneDataNotice,pb.SceneDataNotice)
    assert main.characters[0].character_id==101001 and main.player_name.strip()
    assert main.scene_id==scene.data.scene_id
    return main,scene


def join_after_intro(client):
    """Enter the plaza through the native scene switch for gameplay fixtures that start after the guide."""
    main, scene = join(client)
    if scene.data.scene_id == 100:
        request = pb.ChangeSceneChannelReq(scene_id=9999)
        client.sendall(encode(MsgId.ChangeSceneChannelReq, request.SerializeToString(), 9001))
        reply = next_msg(client, MsgId.ChangeSceneChannelRsp, pb.ChangeSceneChannelRsp)
        assert reply.status == pb.StatusCode_OK and reply.scene_id == 9999
        scene = next_msg(client, MsgId.SceneDataNotice, pb.SceneDataNotice)
        assert scene.data.scene_id == 9999
    return main, scene


def run(root,res):
    identity=LocalIdentity(root/"identity.sqlite3")
    slot,key=identity.create_profile("私服队长")
    service=LocalGameService(identity,root,res,port=0)
    def start():service.start()
    start()
    try:
        assert login(service.port,"unissued-profile-ticket") is None
        with socket.create_connection(("127.0.0.1",service.port),timeout=5) as bad:
            bad.sendall(struct.pack(">H",65535))
            assert not bad.recv(1)
        with socket.create_connection(("127.0.0.1",service.port),timeout=5) as bad:
            header=pb.PacketHead(msg_id=1001,body_len=MAX_BODY+1).SerializeToString()
            bad.sendall(struct.pack(">H",len(header))+header)
            assert not bad.recv(1)
        assert decode_snappy(b"\x0a\x04ab\x1e\x02\x00")==b"ababababab"
        for invalid in (b"\x04\x01\x00", b"\x81\x80\x80\x01", b"\x02\x08abc"):
            try:decode_snappy(invalid)
            except ValueError:pass
            else:raise AssertionError("invalid snappy accepted")
        ticket=identity.issue_ticket(slot,key)
        c,info,pid=login(service.port,ticket)
        assert info.player_name=="私服队长" and info.scene_id==100
        assert login(service.port,ticket) is None
        with c:
            main,scene=join_after_intro(c)
            assert scene.data.players[0].team.char1.weapon_id==1101104
            assert len(scene.data.players)==1 and scene.data.players[0].team.char1.pos.x==2415
            # A peer sees movement immediately without sending a poll. It has its own private player.
            peer_slot,peer_key=identity.create_profile("第二队长")
            peer,peer_info,peer_pid=login(service.port,identity.issue_ticket(peer_slot,peer_key))
            assert peer_pid!=pid
            _,joined=join_after_intro(peer)
            assert {p.player_id for p in joined.data.players}=={pid,peer_pid}
            c.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=12345).SerializeToString(),4))
            assert next_msg(c,MsgId.PlayerPingRsp,pb.PlayerPingRsp).client_time_ms==12345
            req=pb.PlayerSceneRecordReq()
            rec=req.data.char_recorder_data_lst.add(char_id=101001)
            rec.pos.x=0;rec.pos.y=502;rec.pos.z=-741;rec.rot.y=8800
            c.sendall(encode(MsgId.PlayerSceneRecordReq,req.SerializeToString(),5))
            echo=next_msg(c,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            assert echo.data[0].data[0].char_recorder_data_lst[0].pos.z==-741
            observed=next_msg(peer,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
            assert observed.data[0].player_id==pid and observed.data[0].data[0].char_recorder_data_lst[0].pos.z==-741
            peer.close()
            c.sendall(encode(MsgId.SceneSitChairReq,pb.SceneSitChairReq(chair_id=58,seat_id=2,is_sit=True).SerializeToString(),6))
            chair=next_msg(c,MsgId.SceneSitChairRsp,pb.SceneSitChairRsp)
            assert chair.status==pb.StatusCode_OK and chair.chair_id==58 and chair.player_id==pid and chair.is_sit
            # A second genuinely live connection cannot evict the first, even with a fresh valid ticket.
            assert login(service.port,identity.issue_ticket(slot,key)) is None
            c.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=91).SerializeToString(),7))
            assert next_msg(c,MsgId.PlayerPingRsp,pb.PlayerPingRsp).client_time_ms==91
        # Close and reconnect immediately with independent tickets; no sleeps or server restart.
        for _ in range(8):
            fresh=identity.issue_ticket(slot,key)
            reconnected,_,again=login(service.port,fresh)
            with reconnected:
                assert again==pid
                reconnected.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=22).SerializeToString(),8))
                assert next_msg(reconnected,MsgId.PlayerPingRsp,pb.PlayerPingRsp).client_time_ms==22
    finally:service.stop()
    with closing(sqlite3.connect(root/"game.sqlite")) as db, db:
        assert db.execute("SELECT player_name FROM players WHERE player_id=?",(pid,)).fetchone()[0]=="私服队长"
        assert db.execute("SELECT count(*) FROM private_poses").fetchone()[0]==1
        db.execute("UPDATE players SET player_name=' ' WHERE player_id=?",(pid,))
    start()
    try:
        c,info,again=login(service.port,identity.issue_ticket(slot,key))
        with c:
            assert again==pid and info.player_name=="私服队长"
            main,scene=join_after_intro(c); actor=scene.data.players[0].team.char1
            assert (actor.pos.x,actor.pos.y,actor.pos.z,actor.rot.y)==(0,502,-741,8800)
    finally:service.stop()
    with closing(sqlite3.connect(root/"game.sqlite")) as db, db:
        db.execute("UPDATE players SET player_name='已有名字' WHERE player_id=?",(pid,))
    start()
    try:
        c,info,again=login(service.port,identity.issue_ticket(slot,key))
        with c:
            assert again==pid and info.player_name=="已有名字" and join_after_intro(c)[0].player_name=="已有名字"
    finally:service.stop()
    print("PASS: native private ticket/name/login/main-data at guide 100, plaza 9999, ping, pose, sit, restart; blank fixed, existing name preserved.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:run(Path(tmp),args.resources)

