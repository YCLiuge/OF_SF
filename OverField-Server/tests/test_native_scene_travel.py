"""Native social travel resolves the current channel and rechecks home access on entry."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login, next_msg
from test_native_gacha import call


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    host = LocalGameService(identity, root, resources, port=0)
    seq = count(30)

    def request(who, name, **fields):
        return call(who, name, next(seq), **fields)

    def switch(who, channel):
        result = request(who, "ChangeSceneChannel", scene_id=9999, channel_label=channel)
        if result.status == pb.StatusCode_OK:
            next_msg(who, MsgId.SceneDataNotice, pb.SceneDataNotice)
        return result.status

    def denied(reply, status):
        assert reply.status == status and not reply.scene_id and not reply.channel_id
        assert not getattr(reply, "channel_label", 0)

    host.start()
    try:
        with ExitStack() as clients:
            def player(name):
                profile = identity.create_profile(name)
                sock, _, pid = login(host.port, identity.issue_ticket(*profile))
                clients.enter_context(sock)
                join_after_intro(sock)
                return sock, pid

            a, visitor = player("旅行甲")
            b, owner = player("旅行乙")
            def friend():
                return request(a, "TransmitScene", friend_id=owner)
            def locate():
                return request(a, "SceneTransmit", player_id=owner)

            denied(friend(), pb.StatusCode_FRIEND_NOT_EXIST)
            assert switch(b, 2) == pb.StatusCode_OK
            found = locate()
            assert (found.status, found.scene_id, found.channel_id, found.channel_label) == (pb.StatusCode_OK, 9999, 2, 2)
            assert switch(a, found.channel_label) == pb.StatusCode_OK
            assert request(a, "FriendAdd", player_id=owner).status == pb.StatusCode_OK
            assert request(b, "FriendHandle", player_id=visitor, is_agree=True).status == pb.StatusCode_OK
            assert switch(b, owner) == pb.StatusCode_OK
            assert request(b, "SwitchGardenStatus", is_open=True).status == pb.StatusCode_OK
            result = friend()
            assert (result.status, result.scene_id, result.channel_id) == (pb.StatusCode_OK, 9999, owner)
            assert request(b, "SwitchGardenStatus", is_open=False).status == pb.StatusCode_OK
            assert switch(a, result.channel_id) == pb.StatusCode_SCENE_CHANNEL_PRIVATE
            denied(friend(), pb.StatusCode_SCENE_CHANNEL_PRIVATE)
            denied(locate(), pb.StatusCode_SCENE_CHANNEL_PRIVATE)
            assert request(b, "SwitchGardenStatus", is_open=True).status == pb.StatusCode_OK
            assert switch(a, friend().channel_id) == pb.StatusCode_OK
            assert request(b, "DungeonEnter", dungeon_id=11101, char1=101001).status == pb.StatusCode_OK
            denied(locate(), pb.StatusCode_CANT_JOIN_PLAYER_CURRENT_SCENE_CHANNEL)
            denied(request(b, "SceneTransmit", player_id=visitor), pb.StatusCode_CANT_JOIN_PLAYER_CURRENT_SCENE_CHANNEL)
            assert request(b, "DungeonExit").status == pb.StatusCode_OK
            assert request(b, "FriendBlack", player_id=visitor).status == pb.StatusCode_OK
            denied(friend(), pb.StatusCode_FRIEND_BLACK)
            denied(locate(), pb.StatusCode_FRIEND_BLACK)
            assert request(b, "FriendBlack", player_id=visitor, is_remove=True).status == pb.StatusCode_OK
        host.stop()
        host.start()
        with player_after_restart(identity, host) as c:
            join_after_intro(c)
            denied(request(c, "SceneTransmit", player_id=owner), pb.StatusCode_PLAYER_NOT_FOUND)
    finally:
        host.stop()
    print("PASS public and friend travel, private home recheck, dungeon, block and offline rules.")


def player_after_restart(identity, host):
    profile = identity.create_profile("旅行丙")
    sock, _, _ = login(host.port, identity.issue_ticket(*profile))
    return sock


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp), ap.parse_args().resources)
