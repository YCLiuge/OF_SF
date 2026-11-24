"""National field challenge times and personal/friend records over the original TCP protocol."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile("计时" + str(i)) for i in range(2)]
    service = LocalGameService(identity, root, resources, port=0)
    sequence = 40

    def ask(sock, name, **fields):
        nonlocal sequence
        sequence += 1
        return call(sock, name, sequence, **fields)

    def enter(sock, scene=1, channel=1):
        rsp = ask(sock, "ChangeSceneChannel", scene_id=scene, channel_label=channel)
        assert rsp.status == pb.StatusCode_OK
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data

    def attempt(sock, state, elapsed=0, cid=100001):
        return ask(sock, "ChallengeStateUpdate", challenge_id=cid, battle_state=state, use_time=elapsed)

    service.start()
    try:
        with ExitStack() as stack:
            socks, players = [], []
            for profile in profiles:
                sock, _, player = login(service.port, identity.issue_ticket(*profile))
                stack.enter_context(sock)
                join(sock)
                enter(sock)
                socks.append(sock)
                players.append(player)
            a, b = socks
            with service.lock, db.conn:
                db.init_friend(*players)
                for player, friend in (players, players[::-1]):
                    db.set_friend_info(player, friend, "friend_status", pb.FriendStatus_FRIEND)
            assert attempt(a, pb.Finish, 35000).status == pb.StatusCode_ChallengeNotStart
            assert attempt(a, pb.Start, cid=999900001).status == pb.StatusCode_CFG_NOT_FOUND
            assert attempt(a, pb.Start, cid=0).status == pb.StatusCode_CFG_NOT_FOUND
            assert attempt(a, pb.Start).status == pb.StatusCode_OK
            assert attempt(a, pb.Start, cid=100002).status == pb.StatusCode_OTHER_CHALLENGE_STARTED
            assert attempt(a, pb.Finish, 0).status == pb.StatusCode_BAD_REQ
            assert attempt(b, pb.Finish, 35000).status == pb.StatusCode_ChallengeNotStart
            assert attempt(a, pb.Fail).status == pb.StatusCode_OK
            for state in (pb.Close, pb.Reset):
                assert attempt(a, pb.Start).status == pb.StatusCode_OK
                assert attempt(a, state).status == pb.StatusCode_OK
                assert attempt(a, pb.Finish, 35000).status == pb.StatusCode_ChallengeNotStart
            with service.lock:
                balance_before = db.get_item_detail(players[0], 101)
            for elapsed, stars in ((35000, 3), (35001, 2), (40000, 2), (40001, 1), (90000, 1), (90001, 0)):
                assert attempt(a, pb.Start).status == pb.StatusCode_OK
                rsp = attempt(a, pb.Finish, elapsed)
                assert rsp.status == pb.StatusCode_OK
                assert (rsp.challenge_data.use_time, rsp.challenge_data.star) == (elapsed, stars)
                assert not rsp.HasField("dynamic_treasure_box_base_info")
                replay = call(a, "ChallengeStateUpdate", sequence, challenge_id=100001,
                              battle_state=pb.Finish, use_time=elapsed)
                assert replay == rsp
            with service.lock:
                assert db.get_item_detail(players[0], 101) == balance_before
            assert attempt(a, pb.Start).status == pb.StatusCode_OK
            enter(a, channel=2)
            enter(a)
            assert attempt(a, pb.Finish, 32000).status == pb.StatusCode_ChallengeNotStart
            assert attempt(a, pb.Start).status == pb.StatusCode_OK
            assert attempt(a, pb.Finish, 30000).status == pb.StatusCode_OK
            board = ask(b, "ChallengeFriendRank")
            assert not board.self_challenge.challenge_infos
            assert [(r.challenge.player_id, r.challenge.challenge_infos[0].use_time)
                    for r in board.rank_info] == [(players[0], 30000)]
            enter(b, scene=2)
            assert attempt(b, pb.Start).status == pb.StatusCode_BAD_REQ
            assert not ask(b, "ChallengeFriendRank").rank_info[0].challenge.challenge_infos
    finally:
        service.stop()

    service = LocalGameService(identity, root, resources, port=0)
    service.start()
    try:
        sock, _, _ = login(service.port, identity.issue_ticket(*profiles[0]))
        with sock:
            _, notice = join(sock)
            scene = notice.data if notice.data.scene_id == 1 else enter(sock)
            assert [(r.challenge_id, r.use_time, r.star) for r in scene.challenges] == [(100001, 30000, 3)]
            board = ask(sock, "ChallengeFriendRank")
            assert [(r.challenge_id, r.use_time) for r in board.self_challenge.challenge_infos] == [(100001, 30000)]
    finally:
        service.stop()
    print("PASS native field challenge start/fail/reset/close/finish, millisecond thresholds, retries, "
          "scene boundaries, best record, friend visibility and service restart")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as temp:
        run(Path(temp), parser.parse_args().resources)
