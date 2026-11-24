"""Exercise shared plaza fires, timed removal and restart through the native protocol."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import login, join_after_intro, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def fire_notices(sock):
    sock.sendall(encode(MsgId.PlayerPingReq, pb.PlayerPingReq().SerializeToString(), 40))
    events = []
    while True:
        head, raw = receive(sock)
        if head.msg_id == MsgId.PlayerPingRsp and head.packet_id == 40:
            return events
        if head.msg_id == MsgId.PlaceCampFireNotice:
            events.extend(("add", fire.id) for fire in pb.PlaceCampFireNotice.FromString(raw).camp_fires)
        if head.msg_id == MsgId.RemoveCampFireNotice:
            events.extend(("remove", fid) for fid in pb.RemoveCampFireNotice.FromString(raw).camp_fire_id)


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(s) for s in ("篝火甲", "篝火乙", "篝火丙")]
    host = LocalGameService(identity, root, resources, port=0)
    def connect(num, stack):
        sock, _, uid = login(host.port, identity.issue_ticket(*profiles[num]))
        stack.enter_context(sock)
        _, view = join_after_intro(sock)
        return sock, uid, view
    def place(sock, packet, x, action=1050):
        return call(sock, "PlaceCampFire", packet, action_id=action, pos=pb.Vector3(x=x))
    host.start()
    try:
        with patch("private_campfires.now_ms", return_value=1700000000000) as clock:
            with ExitStack() as sockets:
                a, uid, _ = connect(0, sockets)
                b, _, _ = connect(1, sockets)
                c, _, _ = connect(2, sockets)
                assert call(c, "ChangeSceneChannel", 4, scene_id=9999, channel_label=2).status == pb.StatusCode_OK
                next_msg(c, MsgId.SceneDataNotice, pb.SceneDataNotice)
                with host.lock, db.conn:
                    for key in (2050, 2051, 2052):
                        db.set_item_detail(uid, make_item(key, 1, uid).SerializeToString(), key)
                rsp = place(a, 5, 0)
                assert rsp.status == pb.StatusCode_OK and rsp.camp_fire.player_id == uid
                first = rsp.camp_fire.id
                assert place(a, 5, 0) == rsp
                assert fire_notices(b) == [("add", first)] and fire_notices(c) == []
                assert place(a, 6, 590, 1051).status == pb.StatusCode_CAMP_FIRE_TOO_CLOSE
                assert place(a, 7, 900).status == pb.StatusCode_ITEM_NOT_ENOUGH
                assert call(b, "RemoveCampFire", 8, camp_fire_id=first).status == pb.StatusCode_BAD_REQ
                assert call(a, "RemoveCampFire", 9, camp_fire_id=first).status == pb.StatusCode_OK
                assert fire_notices(b) == [("remove", first)] and fire_notices(c) == []
                second = place(a, 10, 0, 1051)
                third = place(a, 11, 800, 1052)
                assert second.status == third.status == pb.StatusCode_OK
                assert second.camp_fire.id > first and third.camp_fire.id > second.camp_fire.id
            host.stop()
            host.start()
            with ExitStack() as sockets:
                a, _, view = connect(0, sockets)
                assert [f.id for f in view.data.camp_fires] == [second.camp_fire.id, third.camp_fire.id]
                clock.return_value += 300000
                expired = next_msg(a, MsgId.RemoveCampFireNotice, pb.RemoveCampFireNotice)
                assert list(expired.camp_fire_id) == [second.camp_fire.id]
                assert call(a, "RemoveCampFire", 12, camp_fire_id=second.camp_fire.id).status == pb.StatusCode_BAD_REQ
                assert call(a, "RemoveCampFire", 13, camp_fire_id=third.camp_fire.id).status == pb.StatusCode_OK
    finally:
        host.stop()
    print("PASS campfire placement, debit, retry, distance, owner removal, channel isolation, restart and idle expiry.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
