"""Exercise movement batching, native scene visibility and recovery over loopback TCP."""
import argparse
import json
from contextlib import ExitStack, closing
from pathlib import Path
import socket
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, Session, pb
from local_game_wire import MAX_BODY, broadcast_frames, encode, receive
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login, next_msg
from test_observed_initialization import call
from utils import db


def movement(sock, *, char_id=101001, pos=None, rot=None):
    req = pb.PlayerSceneRecordReq()
    actor = req.data.char_recorder_data_lst.add(char_id=char_id)
    if pos is not None:
        actor.pos.x = pos
    if rot is not None:
        actor.rot.y = rot
    sock.sendall(encode(MsgId.PlayerSceneRecordReq, req.SerializeToString(), 11))


def fence(sock, removals=None):
    """An ordered ping ends the list of broadcasts already queued for this client."""
    sock.sendall(encode(MsgId.PlayerPingReq, pb.PlayerPingReq(client_time_ms=123).SerializeToString(), 12))
    records, actions = [], []
    while True:
        head, body = receive(sock)
        if head.msg_id == MsgId.PlayerPingRsp and head.packet_id == 12:
            return records, actions
        if removals is not None and head.msg_id == MsgId.SceneGardenFurnitureRemoveNotice:
            removals.append(pb.SceneGardenFurnitureRemoveNotice.FromString(body).furniture_id)
        if head.msg_id == MsgId.PlayerSceneSyncDataNotice:
            for player in pb.PlayerSceneSyncDataNotice.FromString(body).data:
                records.extend((player.player_id, rec) for rec in player.data)
        if head.msg_id == MsgId.ServerSceneSyncDataNotice:
            for player in pb.ServerSceneSyncDataNotice.FromString(body).data:
                actions.extend((player.player_id, act.action_type) for act in player.server_data)


def check_fanout():
    channel = (9999, 1)
    host = SimpleNamespace(scene_members={}, pending_publish=set())
    notice = pb.PlayerSceneSyncDataNotice(status=pb.StatusCode_OK)
    message_id = MsgId.PlayerSceneSyncDataNotice
    body = notice.SerializeToString()
    starts = (1, 127, 16384, 0xffffffff, 127)
    with ExitStack() as stack:
        peers = []
        for i, start in enumerate(starts):
            sock = stack.enter_context(socket.socket())
            peer = Session(sock, str(i), i + 1, str(i))
            peer.sequence = start
            peer.pending_publish = host.pending_publish
            peers.append(peer)
        host.scene_members[channel] = set(peers)
        def stage():
            LocalGameService._broadcast_scene(host, channel, message_id, notice)
        stage()
        assert peers[1].responses[0] is peers[-1].responses[0]
        for peer, start in zip(peers, starts):
            assert peer.responses == [encode(message_id, body, 0, start)]
            assert peer.outbox.empty() and peer.outstanding == 0
        LocalGameService._discard_pending(host)
        assert [p.sequence for p in peers] == list(starts) and not host.pending_publish
        assert all(not p.responses and p.outbox.empty() for p in peers)
        stage()
        stage()
        LocalGameService._publish_pending(host)
        for peer, start in zip(peers, starts):
            next_seq = 1 if start == 0xffffffff else start + 1
            sent = [peer.outbox.get_nowait(), peer.outbox.get_nowait()]
            assert sent == [encode(message_id, body, 0, n) for n in (start, next_seq)]
            assert peer.outstanding == sum(map(len, sent))
        slow = peers[-1]
        bytes_before_rejection = slow.outstanding
        for _ in range(slow.outbox.maxsize):
            slow.outbox.put_nowait(b"queued")
        stage()
        LocalGameService._publish_pending(host)
        assert not slow.running and all(p.running and p.outbox.qsize() == 1 for p in peers[:-1])
        assert slow.outstanding == bytes_before_rejection
        oversized = bytes(MAX_BODY + 1)
        with TestCase().assertRaisesRegex(ValueError, "response too large"):
            peers[0].send(message_id, oversized, 0, is_bin=True, broadcast=broadcast_frames(message_id, oversized))
        peers[0].responses = [b"group"] * 512
        with TestCase().assertRaisesRegex(ValueError, "too many responses"):
            peers[0].send(message_id, oversized, 0, is_bin=True, broadcast=broadcast_frames(message_id, oversized))


def run(root, resources):
    check_fanout()
    identity = LocalIdentity(root / "identity.sqlite3")
    host = LocalGameService(identity, root, resources, port=0)
    # One explicit idle tick below exercises the normal accept-loop checkpoint.
    host.pose_flush_interval = 3600
    profiles = [identity.create_profile(name) for name in ("姿态甲", "姿态乙", "姿态丙")]

    def connect(profile):
        sock, _, uid = login(host.port, identity.issue_ticket(*profile))
        _, view = join_after_intro(sock)
        return sock, uid, view

    def switch(sock, channel):
        assert call(sock, "ChangeSceneChannel", scene_id=9999, channel_label=channel).status == pb.StatusCode_OK
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice)

    def stored(uid, instance=False):
        sql = ("SELECT pos,rot FROM private_dungeon_poses WHERE player=? AND dungeon=11101"
               if instance else "SELECT pos,rot FROM private_poses WHERE player_id=? AND scene=9999")
        with closing(sqlite3.connect(root / "game.sqlite")) as reader:
            row = reader.execute(sql, (uid,)).fetchone()
        if row is not None:
            return pb.Vector3.FromString(row[0]).x, pb.Vector3.FromString(row[1]).y

    check = TestCase()
    host.start()
    try:
        with ExitStack() as sockets:
            a, uid, _ = connect(profiles[0])
            b, bid, _ = connect(profiles[1])
            c, _, _ = connect(profiles[2])
            for sock in (a, b, c):
                sockets.enter_context(sock)
            with host.lock:
                member = host.active[profiles[0][0]]
                sequence = member.sequence
                with patch("local_game_service.encode", side_effect=ValueError("invalid frame")), check.assertRaises(ValueError):
                    member.send(MsgId.PlayerPingRsp, pb.PlayerPingRsp(), 1)
                assert member.sequence == sequence and not member.responses and not host.pending_publish
            switch(c, 2)
            for sock in (a, b, c):
                fence(sock)
            for x in range(1, 41):
                movement(a, pos=x)
            movement(a, rot=82)
            movement(a, char_id=999999, pos=999999)
            records, _ = fence(a)
            assert len(records) == 42 and all(player == uid for player, _ in records)
            assert fence(b)[0] == records and not fence(c)[0]
            assert stored(uid) is None
            # A failed disk checkpoint must leave the most recent frame available to retry.
            with host.lock:
                # A scene refresh may replace the live actor between the network
                # event and a checkpoint. Save the last accepted movement.
                live = member.scene_player.team.char1
                live.pos.x, live.rot.y = -9876, -9877
                db.conn.execute("CREATE TEMP TRIGGER fail_pose BEFORE INSERT ON private_poses BEGIN SELECT RAISE(ABORT,'test'); END")
                try:
                    host._flush_poses()
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError("checkpoint unexpectedly succeeded")
                db.conn.execute("DROP TRIGGER fail_pose")
                host.next_pose_flush = 0
            deadline = time.monotonic() + 5
            while stored(uid) is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert stored(uid) == (40, 82)
            with host.lock:
                live.pos.x, live.rot.y = 40, 82

            movement(a, pos=41)
            switch(a, 2)
            assert stored(uid) == (41, 82)
            movement(a, rot=83)
            assert len(fence(a)[0]) == 1 and len(fence(c)[0]) == 1
            # b received frame 41 in the old channel only.
            assert len(fence(b)[0]) == 1
            assert call(a, "DungeonEnter", dungeon_id=11101, char1=101001).status == pb.StatusCode_OK
            assert stored(uid) == (41, 83)
            spawn = stored(uid, instance=True)
            movement(a, pos=-5000)
            movement(a, rot=7777)
            assert len(fence(a)[0]) == 2 and not fence(c)[0]
            assert stored(uid, instance=True) == spawn
            assert call(a, "DungeonExit").status == pb.StatusCode_OK
            back = next_msg(a, MsgId.SceneDataNotice, pb.SceneDataNotice)
            lead = next(p for p in back.data.players if p.player_id == uid).team.char1
            assert (lead.pos.x, lead.rot.y) == (41, 83)
            assert stored(uid, instance=True) == (-5000, 7777)
            movement(a, pos=42)
            assert len(fence(a)[0]) == 1 and len(fence(c)[0]) == 1 and not fence(b)[0]

            # Simulate an exception after indexing and queuing a channel switch, before commit.
            # Other clients must see neither the rolled-back enter nor any partial replies.
            drain = host._drain_scene_events
            def reject_switch():
                drain()
                raise sqlite3.OperationalError("simulated failed commit")

            with patch.object(host, "_drain_scene_events", reject_switch):
                req = pb.ChangeSceneChannelReq(scene_id=9999, channel_label=2)
                b.sendall(encode(MsgId.ChangeSceneChannelReq, req.SerializeToString(), 31))
                with host.slot_available:
                    assert host.slot_available.wait_for(lambda: profiles[1][0] not in host.active, timeout=5)
            assert (bid, pb.SceneActionType_ENTER) not in fence(c)[1]
            assert (bid, pb.SceneActionType_ENTER) not in fence(a)[1]
            with host.lock:
                assert not host.pending_publish
                assert not any(p.player_id == bid for group in host.scene_members.values() for p in group)
            b2, uid2, initial = connect(profiles[1])
            sockets.enter_context(b2)
            assert bid == uid2 and initial.data.channel_label == 1

            # Roll back a real furniture removal and fail the final pose write during TCP reset.
            import private_channels
            template = json.loads((resources/"data/PlazaMusic.json").read_bytes())["instruments"][0]
            placed = call(a, "PlaceFurniture", furniture_item_id=template, pos={"x":1}, rot={"y":2}, layer_num=1)
            fid = placed.furniture_details_info.furniture_id
            assert placed.status == pb.StatusCode_OK and fid
            fence(c)
            reclaim = private_channels.reclaim
            def reject_reclaim(*args, **kwargs):
                reclaim(*args, **kwargs)
                raise sqlite3.OperationalError("failed reclaim")

            with host.lock:
                db.conn.execute("CREATE TEMP TRIGGER fail_exit BEFORE INSERT ON private_poses BEGIN SELECT RAISE(ABORT,'test'); END")
            with patch.object(private_channels, "reclaim", reject_reclaim), check.assertLogs("overfield.local.game"):
                a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                             struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0))
                a.close()
                with host.slot_available:
                    assert host.slot_available.wait_for(lambda: profiles[0][0] not in host.active, timeout=5)
            removals=[]
            assert (uid, pb.SceneActionType_LEAVE) in fence(c, removals)[1] and not removals
            with host.lock:
                assert uid in host.pending_poses and uid in host.pending_reclaims and not host.pending_publish
                db.conn.execute("DROP TRIGGER fail_exit")
            a2, uid2, saved = connect(profiles[0])
            sockets.enter_context(a2)
            lead = next(p for p in saved.data.players if p.player_id == uid).team.char1
            assert uid2 == uid and (lead.pos.x, lead.rot.y) == (42, 83)
            fence(c, removals)
            assert removals == [fid]
            movement(a2, pos=43)
            movement(b2, pos=77, rot=88)
            assert len(fence(a2)[0]) == 1 and len(fence(b2)[0]) == 1
            assert stored(uid) == (42,83)
            start = time.monotonic()
            host.stop()  # Live sockets remain open until stop returns.
            assert time.monotonic() - start < 10
            assert stored(uid) == (43,83) and stored(bid) == (77,88)
            assert not host.scene_members and not host.pending_publish
        # Failed initial account commit must release its seat without exposing a phantom session.
        host.start()
        failed = identity.create_profile("入场测试")
        def deny_commit(action, name, *_):
            if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        with host.lock:
            db.conn.set_authorizer(deny_commit)
        with socket.create_connection(("127.0.0.1",host.port),timeout=3) as blocked:
            req = pb.VerifyLoginTokenReq(login_token=identity.issue_ticket(*failed))
            blocked.sendall(encode(MsgId.VerifyLoginTokenReq, req.SerializeToString(), 1))
            assert blocked.recv(1) == b""
        with host.lock:
            db.conn.set_authorizer(None)
            assert failed[0] not in host.active
            from server import scene_data
            assert not scene_data._sessions_by_player
        connected, _, _ = connect(failed)
        with connected:
            pass

        # Keep a dirty pose through permanent storage failure at shutdown, then retry on restart.
        connected, _, _ = connect(profiles[0])
        with connected:
            movement(connected,pos=44)
            fence(connected)
            with host.lock:
                db.conn.execute("CREATE TEMP TRIGGER fail_stop BEFORE INSERT ON private_poses BEGIN SELECT RAISE(ABORT,'test'); END")
            with check.assertLogs("overfield.local.game"), check.assertRaises(sqlite3.IntegrityError):
                host.stop()
        assert host.listener is None and host.thread is None and db.conn is None and uid in host.pending_poses
        host.start()
        assert stored(uid) == (44,83) and uid not in host.pending_poses
    finally:
        host.stop()
    print("PASS native movement fan-out, partial poses, idle checkpoint/retry, channel and dungeon transitions, "
          "transaction rollback, reset/reconnect and graceful stop.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        run(Path(temp), args.resources)
