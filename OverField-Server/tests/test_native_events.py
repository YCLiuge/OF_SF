"""Replay the original daily dessert page, claims, potions and live owner controls."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from private_shop import CN, balance
from realm_events import DAILY, available, configure
from test_game_realm import login


GOAL = 80400801
NOTICES = {getattr(MsgId, name): getattr(pb, name) for name in (
    "AchieveNotice", "ActivityChangeNotice", "ActivityRegularDataNotice", "ActivitySignInDataNotice", "ActivityQuestDataNotice")}


def rpc(sock, name, packet, **fields):
    req = getattr(pb, name + "Req")(**fields)
    sock.sendall(encode(getattr(MsgId, name + "Req"), req.SerializeToString(), packet))
    info = []
    for _ in range(200):
        head, body = receive(sock)
        if head.msg_id in NOTICES:
            info.append(NOTICES[head.msg_id].FromString(body))
        elif head.msg_id == getattr(MsgId, name + "Rsp") and head.packet_id == packet:
            return getattr(pb, name + "Rsp").FromString(body), info
    raise AssertionError(name)


def dessert(frames):
    return [goal for msg in frames if isinstance(msg, pb.ActivityRegularDataNotice)
            for info in msg.info if info.activity_id == DAILY for goal in info.daily_achieve]


def toggled(frames, opened):
    return {aid for msg in frames if isinstance(msg, pb.ActivityChangeNotice) and msg.is_open == opened
            for aid in msg.activity_id}


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    slots = [identity.create_profile(name) for name in ("每日甲", "每日乙")]
    service = LocalGameService(identity, root, resources, port=0)
    when = [datetime(2026, 11, 24, 4, 59, 59, tzinfo=CN)]
    catalog = {item["id"]: item for item in available()}
    assert catalog[DAILY]["name"] == "每日甜点" and 2001 in catalog
    expected = {r["item_i_d"]: r["item_num"] for r in catalog[DAILY]["rewards"][0]["items"]}
    assert expected == {170: 6, 180: 6, 191: 2}
    assert configure(root) == {"events": []}

    def connect(i):
        sock, _, pid = login(service.port, identity.issue_ticket(*slots[i]))
        main, early = rpc(sock, "PlayerMainData", 3)
        assert main.status == pb.StatusCode_OK
        _, later = rpc(sock, "PlayerPing", 4)
        return sock, pid, early + later

    def values(pid, items):
        with service.lock:
            return {item: (balance(pid, item) or pb.ItemDetail()).main_item.base_item.num for item in items}

    def claim(sock, seq, goal=GOAL, pool=1):
        return rpc(sock, "ActivityRegularReward", seq, activity_id=DAILY, param=goal, pool_type=pool)

    def apply(*entries):
        config = {"events": [{"id": aid, "enabled": enabled, "opens_at": opens, "closes_at": closes}
                             for aid, enabled, opens, closes in entries]}
        assert configure(root, config) == config

    def heartbeat(sock, seq):
        # The host emits the change notices after the ping reply. The next frame bounds the batch.
        _, first = rpc(sock, "PlayerPing", seq)
        _, second = rpc(sock, "PlayerPing", seq+1)
        return first + second

    with patch("private_shop.now", side_effect=lambda: when[0]):
        service.start()
        try:
            first, pid, snapshot = connect(0)
            other, peer, _ = connect(1)
            with first, other:
                assert DAILY in toggled(snapshot, True) and 260730104 in toggled(snapshot, True)
                goals = dessert(snapshot)
                assert len(goals) == 1 and goals[0].achieve_id == GOAL and not goals[0].is_reward
                assert any(a.achieve_id == GOAL and a.count == 1 for m in snapshot if isinstance(m, pb.AchieveNotice) for a in m.achieves)
                before, neighbor = values(pid, expected), values(peer, expected)
                assert claim(first, 10, pool=2)[0].status == pb.StatusCode_BAD_REQ
                assert claim(first, 11, goal=4008)[0].status == pb.StatusCode_BAD_REQ
                reply, notice = claim(first, 12)
                assert reply.status == pb.StatusCode_OK and reply.param == GOAL and reply.pool_type == 1
                assert {d.main_item.item_id: d.main_item.base_item.num for d in reply.rewards} == expected
                assert dessert(notice)[0].is_reward and values(peer, expected) == neighbor
                assert values(pid, expected) == {k: before[k] + num for k, num in expected.items()}
                assert claim(first, 12)[0].status == pb.StatusCode_REPEAT_GET_REWARD
                state = values(pid, (10, 11, 170, 180))
                for potion, currency, amount in ((170, 11, 50), (180, 10, 30)):
                    result, _ = rpc(first, "ItemUse", potion, item_id=potion, num=2)
                    assert result.status == pb.StatusCode_OK
                    assert values(pid, (currency, potion)) == {currency: state[currency]+amount*2, potion: state[potion]-2}
                    assert rpc(first, "ItemUse", potion, item_id=potion, num=2)[0] == result
                    assert values(pid, (currency,))[currency] == state[currency]+amount*2
                when[0] = when[0].replace(hour=5, minute=0, second=0)
                assert not dessert(heartbeat(first, 20))[0].is_reward and claim(first, 22)[0].status == pb.StatusCode_OK
                assert claim(other, 23)[0].status == pb.StatusCode_OK
                apply((DAILY, False, None, None), (2001, False, None, None))
                assert {DAILY, 2001} <= toggled(heartbeat(first, 24), False)
                assert claim(first, 26)[0].status == pb.StatusCode_Activity_Close
                assert rpc(first, "ActivitySignIn", 27, activity_id=2001, day=1)[0].status == pb.StatusCode_Activity_Close
                apply((DAILY, True, "2026-11-24 06:00:00", "2026-11-24 06:01:00"))
                assert claim(first, 28)[0].status == pb.StatusCode_Activity_Close
                when[0] = when[0].replace(hour=6)
                assert dessert(heartbeat(first, 29))[0].is_reward
                when[0] = when[0].replace(minute=1, second=1)
                assert DAILY in toggled(heartbeat(first, 31), False)
                apply()
                assert DAILY in toggled(heartbeat(first, 33), True)
        finally:
            service.stop()
        service.start()
        try:
            first, _, snapshot = connect(0)
            with first:
                assert dessert(snapshot)[0].is_reward and claim(first, 35)[0].status == pb.StatusCode_REPEAT_GET_REWARD
                try:
                    apply((260730104, True, "2025-01-01 00:00:00", None))
                except ValueError:
                    pass
                else:
                    raise AssertionError("An override extended the actual native calendar")
        finally:
            service.stop()
    print("PASS native daily dessert tab, login goal, reward preview, claim, potion use, 05:00 reset, hot event calendar, isolation, retries and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
