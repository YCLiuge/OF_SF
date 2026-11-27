"""Verify original game login and cross-node scene traffic on two real OF_SF processes."""
import argparse
from contextlib import ExitStack, closing, contextmanager
import http.client
import json
import os
from pathlib import Path
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from local_game_wire import encode, receive
from test_federation_world import make_configs
from test_game_realm import MsgId, pb, join_after_intro, login, next_msg
from test_observed_initialization import call


SERVER = Path(__file__).resolve().parents[1]


def ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]


def request_json(port, method, path, fields=None):
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=2)) as client:
        body = json.dumps(fields).encode() if fields is not None else None
        client.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = client.getresponse()
        assert response.status == 200, (path, response.status)
        return json.loads(response.read())


@contextmanager
def realm(state, config, http_port, game_port, first):
    env = {k:v for k,v in os.environ.items() if not k.startswith("OF_")}
    env.update(OF_BIND="127.0.0.1", OF_GAME_HOST="127.0.0.1", OF_HTTP_PORT=str(http_port),
               OF_GAME_PORT=str(game_port), OF_FEDERATION_CONFIG=str(config))
    cli = [sys.executable, "-B", str(SERVER / "of.py"), "--state", str(state)]
    def command(*args):
        subprocess.run([*cli,*args], cwd=SERVER, env=env, check=True, stdout=subprocess.DEVNULL,
                       timeout=12)
    command("rules", "--uid-start", str(first))
    profile = state / "fixture-profile.json"
    command("create-player", "--nickname", "player-" + state.name, "--out", str(profile))
    keys = json.loads(profile.read_bytes())
    proc = subprocess.Popen(cli + ["start"], cwd=SERVER, env=env, stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Realm {state.name} exited with code {proc.returncode}")
            try:
                if request_json(http_port, "GET", "/local/v1/region")["game_port"] == game_port:
                    break
            except OSError:
                time.sleep(.08)
        else:
            raise TimeoutError(f"Realm {state.name} did not start")
        yield http_port, game_port, {k: keys[k] for k in ("slot","profile_key")}
    finally:
        if proc.poll() is None:
            try:
                command("stop")
                assert proc.wait(12) == 0
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(5)


def enter(node, expected):
    http_port, game_port, keys = node
    ticket = request_json(http_port, "POST", "/local/v1/sessions", keys)["ticket"]
    sock, info, uid = login(game_port, ticket)
    try:
        assert uid == expected, (uid, expected)
        main, scene = join_after_intro(sock)
        assert main.player_name == info.player_name and scene.data.scene_id == 9999
        return sock
    except BaseException:
        sock.close()
        raise


def switch(sock, scene, channel):
    result = call(sock, "ChangeSceneChannel", scene_id=scene, channel_label=channel)
    assert result.status == pb.StatusCode_OK, (scene, channel, result.status)
    snapshot = next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data
    assert (snapshot.scene_id, snapshot.channel_id) == (scene, channel)
    return {p.player_id for p in snapshot.players}


def move(sock, z):
    req = pb.PlayerSceneRecordReq()
    rec = req.data.char_recorder_data_lst.add(char_id=101001)
    rec.pos.x, rec.pos.z = 520, z
    sock.sendall(encode(MsgId.PlayerSceneRecordReq, req.SerializeToString(), 61))


def events(header, body):
    if header.msg_id == MsgId.ServerSceneSyncDataNotice:
        for actor in pb.ServerSceneSyncDataNotice.FromString(body).data:
            for rec in actor.server_data:
                if rec.action_type == pb.SceneActionType_ENTER:
                    assert rec.player.player_id == actor.player_id
                    yield "enter", actor.player_id, rec.player.player_name
                elif rec.action_type == pb.SceneActionType_LEAVE:
                    yield "leave", actor.player_id, None
    if header.msg_id == MsgId.PlayerSceneSyncDataNotice:
        for actor in pb.PlayerSceneSyncDataNotice.FromString(body).data:
            for sample in actor.data:
                for rec in sample.char_recorder_data_lst:
                    yield "move", actor.player_id, rec.pos.z


def observe(sock, wanted, seconds=7, *, absent=False):
    until = time.monotonic() + seconds
    seen = []
    while (remaining := until - time.monotonic()) > 0:
        if not select.select([sock], [], [], remaining)[0]:
            break
        for event in events(*receive(sock)):
            seen.append(event)
            if event == wanted:
                assert not absent, ("cross-channel leak", event)
                return
    assert absent, ("missing", wanted, seen[-8:])


def run(tmp):
    a_uid, b_uid = 1000001, 2000001
    configs = make_configs(tmp / "certs", ("a","b"), {9999:"a", 1:"b"},
                           uid_ranges={"a":(a_uid,1999999), "b":(b_uid,2999999)})
    http_a, game_a, http_b, game_b = ports(4)
    with ExitStack() as stack:
        a = stack.enter_context(realm(tmp/"a", configs["a"], http_a, game_a, a_uid))
        b = stack.enter_context(realm(tmp/"b", configs["b"], http_b, game_b, b_uid))
        one = stack.enter_context(enter(a, a_uid))
        two = stack.enter_context(enter(b, b_uid))
        observe(one, ("enter",b_uid,"player-b"))
        observe(two, ("enter",a_uid,"player-a"))
        move(two, -322)
        observe(one, ("move",b_uid,-322))
        move(one, -721)
        observe(two, ("move",a_uid,-721))

        switch(two, 9999, 2)
        observe(one, ("leave",b_uid,None))
        move(two,-323)
        observe(two, ("move",b_uid,-323))
        observe(one, ("move",b_uid,-323), 1.3, absent=True)

        # Scene 1 has the other owner. Both players stay on their original game sockets.
        switch(one, 1, 1)
        switch(two, 1, 1)
        observe(one, ("enter",b_uid,"player-b"))
        observe(two, ("enter",a_uid,"player-a"))
        move(two, -324)
        observe(one, ("move",b_uid,-324))

        # Scene 2 has no federation owner; only its local player's native state is visible.
        assert switch(two, 2, 1) == {b_uid}
        observe(one, ("leave",b_uid,None))
        assert switch(one, 2, 1) == {a_uid}
        move(two,-325)
        observe(two, ("move",b_uid,-325))
        observe(one, ("move",b_uid,-325), 1.3, absent=True)

        switch(one,9999,1)
        switch(two,9999,1)
        observe(one, ("enter",b_uid,"player-b"))
        two.close()
        observe(one, ("leave",b_uid,None))
        # A fresh ticket recovers the same home character, and the remote avatar reappears.
        with enter(b, b_uid):
            observe(one, ("enter",b_uid,"player-b"))
        observe(one, ("leave",b_uid,None))

    for name in ("a","b"):
        with closing(sqlite3.connect((tmp / name / "game.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
            assert db.execute("SELECT player_name FROM players").fetchall() == [("player-"+name,)]
    print("PASS two running OF_SF realms, separate private tickets and stores, both native scene owners, "
          "cross-node ENTER/MOVE/LEAVE, channel isolation, local-only scene, disconnect and rejoin.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, help="accepted by the server test runner")
    parser.parse_args()
    if shutil.which("openssl"):
        with TemporaryDirectory() as tmp:
            run(Path(tmp))
    else:
        print("SKIP native two-node scene test: openssl required for disposable certificates")
