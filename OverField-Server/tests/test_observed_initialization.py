"""Replay the unmodified 1.1.12 client's observed post-login initialization sequence."""
import argparse
import json
import os
import socket
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from local_game_wire import encode, receive
from test_game_realm import login


def call(sock, name, **fields):
    req = getattr(pb,name+"Req")(**fields)
    sock.sendall(encode(getattr(MsgId,name+"Req"),req.SerializeToString(),27))
    for _ in range(150):
        h,b=receive(sock)
        if h.packet_id==27 and h.msg_id==getattr(MsgId,name+"Rsp"):
            return getattr(pb,name+"Rsp").FromString(b)
    raise AssertionError(name)


def initialize(port, ticket):
    sock, _, pid = login(port, ticket)
    calls = ("PlayerMainData", "PlayerAbilityList", "GachaList", "PosterIllustrationList", "WorldLevelAchieveList", "WishListByFriendId", "PlayerPing")
    frames = []
    for packet, name in enumerate(calls, 3):
        req = getattr(pb, name+"Req")()
        if name=='WishListByFriendId': req.player_id=pid
        frames.append(encode(getattr(MsgId,name+"Req"),req.SerializeToString(),packet))
    sock.sendall(b"".join(frames))
    replies = {}
    for _ in range(200):
        header, body = receive(sock)
        if header.packet_id not in range(3,3+len(calls)):
            continue
        name = calls[header.packet_id-3]
        if header.msg_id != getattr(MsgId,name+"Rsp"):
            continue
        rsp = getattr(pb,name+"Rsp").FromString(body)
        assert rsp.status == pb.StatusCode_OK
        if name=="WishListByFriendId": assert rsp.player_id==pid and not rsp.wish_list and not rsp.week_send_count
        if name=="WorldLevelAchieveList": assert rsp.achieves and all(x.count==0 for x in rsp.achieves)
        replies[name] = rsp
        if len(replies)==len(calls):
            return sock, pid, replies["PosterIllustrationList"]
    raise AssertionError("Missing native initialization replies")


def run(root, resources):
    with patch.dict(os.environ,{"OF_STAGE_DIAGNOSTICS":"1","OF_QUICKSDK_VERIFY_URL":""}):
        identity=LocalIdentity(root/"identity.sqlite3")
        one=identity.create_profile("映像一")
        two=identity.create_profile("映像二")
        game=LocalGameService(identity,root,resources,port=0)
        game.start()
        try:
            sock,pid,initial=initialize(game.port,identity.issue_ticket(*one))
            with sock:
                assert not initial.poster_illustrations
            # A real poster instance from PC 1.1.12 maps to the matching visible catalog entry.
            item=pb.ItemDetail()
            item.main_item.item_id=10010
            item.main_item.item_tag=pb.EBagItemTag_Poster
            item.main_item.poster.poster_id=10010
            item.main_item.poster.instance_id=321123
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                db.execute("INSERT INTO items_s(player_id,instance_id,item_detail_blob) VALUES(?,?,?)",
                           (pid,321123,item.SerializeToString()))
                db.commit()
            sock,other_pid,other=initialize(game.port,identity.issue_ticket(*two))
            with sock:
                assert not other.poster_illustrations
                with closing(sqlite3.connect(root/"game.sqlite")) as db:
                    db.execute("INSERT INTO private_wishes VALUES(?,?,?,?,?)",(pid,5,4,3,2))
                    db.commit()
                assert call(sock,"WishListByFriendId",player_id=pid).status==pb.StatusCode_FRIEND_NOT_EXIST
                assert not call(sock,"WishListByShopId",shop_id=5,grid_id=4).info
                with closing(sqlite3.connect(root/"game.sqlite")) as db:
                    db.execute("INSERT INTO friend(player_id,friend_id,friend_status,alias) VALUES(?,?,2,'friend')",(other_pid,pid))
                    db.commit()
                wish=call(sock,"WishListByFriendId",player_id=pid)
                assert wish.status==pb.StatusCode_OK and wish.player_id==pid
                assert [(w.shop_id,w.grid_id,w.pool_id,w.create_time) for w in wish.wish_list]==[(5,4,3,2)]
                shoppers=call(sock,"WishListByShopId",shop_id=5,grid_id=4)
                assert shoppers.status==pb.StatusCode_OK and [(f.info.player_id,f.alias) for f in shoppers.info]==[(pid,"friend")]
                assert not call(sock,"WishListByShopId",shop_id=5,grid_id=0).info
                with closing(sqlite3.connect(root/"game.sqlite")) as db:
                    db.execute("DELETE FROM private_wishes WHERE player_id=?",(pid,))
                    db.commit()
            sock,again,owned=initialize(game.port,identity.issue_ticket(*one))
            with sock:
                assert pid==again
                assert [(x.poster_illustration_id,x.status) for x in owned.poster_illustrations]==[(10010,pb.Reward)]
                sock.sendall(encode(MsgId.PosterIllustrationRewardReq,pb.PosterIllustrationRewardReq().SerializeToString(),9))
                h,raw=receive(sock)
                assert h.msg_id==MsgId.PosterIllustrationRewardRsp and h.packet_id==9
                assert pb.PosterIllustrationRewardRsp.FromString(raw).status==pb.StatusCode_FAIL
                sock.sendall(encode(MsgId.PlayerPingReq,b"",10))
                h,raw=receive(sock)
                assert h.msg_id==MsgId.PlayerPingRsp and h.packet_id==10
                # A server-side notice cannot be used as a client request and cannot receive a fabricated response.
                sock.sendall(encode(MsgId.SceneDataNotice,b"",11))
                assert not sock.recv(1)
            # The handshake times out if a client opens its socket but never transmits.
            with socket.create_connection(("127.0.0.1",game.port),timeout=12) as idle:
                idle.settimeout(12)
                assert not idle.recv(1)
        finally:
            game.stop()
        entries = [json.loads(s) for s in game.stages.path.read_bytes().splitlines()]
        assert {"close_reason":"eof"} in entries
        assert {"close_reason":"unsupported_message"} in entries
        assert {"close_reason":"timeout"} in entries
        assert {"unsupported_msg_id":MsgId.PosterIllustrationRewardReq} in entries
    print("PASS observed 1005,1611,1443,1423,1523,2685+ping, original poster mapping, isolation, reconnection and close reasons.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources)
