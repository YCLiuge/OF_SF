"""Check the SQLite inventory snapshot against the two-table native login packets."""
import argparse
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
import tracemalloc
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, db, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
import realm_uids
from handlers import PlayerMainData
from private_treasure_dig import repair
from test_game_realm import login
from utils.res_loader import res


def original_packets(player):
    # Keep the reference separate from the new cursor and grouping implementation.
    rows = db.get_item_detail(player)
    groups = {}
    for raw in rows:
        item = pb.ItemDetail()
        item.ParseFromString(raw)
        if item.main_item.HasField("treasure"):
            repair(player, item)
        tag = item.main_item.item_tag
        if tag not in groups:
            groups[tag] = []
        groups[tag].append(item)
    packets = []
    for group in groups.values():
        packet = pb.PackNotice(status=pb.StatusCode_OK)
        for item in group:
            packet.items.add().CopyFrom(item)
        packets.append(packet)
    return packets


def serialized(packets):
    return [realm_uids.outgoing(packet).SerializeToString() for packet in packets]


def game_inventory(sock):
    sock.sendall(encode(MsgId.PlayerMainDataReq, pb.PlayerMainDataReq().SerializeToString(), 3))
    frames = []
    for _ in range(200):
        head, raw = receive(sock)
        if head.msg_id == MsgId.PackNotice:
            frames.append(raw)
        if head.msg_id == MsgId.SceneDataNotice:
            return frames
    raise AssertionError("Missing scene after login inventory")


def add_instances(player):
    # Synthetic records using national item IDs, with interleaved types like a progressed save.
    tags = {pb.EBagItemTag_Weapon: "weapon", pb.EBagItemTag_Armor: "armor", pb.EBagItemTag_Poster: "poster"}
    ids = {}
    for row in res["Item"]["item"]["datas"]:
        tag = row.get("new_bag_item_tag")
        if tag in tags:
            ids.setdefault(tag, row["i_d"])
    assert set(ids) == set(tags)
    for i in range(93):
        tag = list(tags)[i % len(tags)]
        kind = tags[tag]
        detail = pb.ItemDetail()
        item = detail.main_item
        item.item_id, item.item_tag = ids[tag], tag
        gear = getattr(item, kind)
        gear.instance_id = 90000 + i
        setattr(gear, kind + "_id", item.item_id)
        if kind != "poster":
            gear.level = 12
            gear.random_property.add(value=120)
        detail.extras.add(item_id=101, item_tag=pb.EBagItemTag_Currency)
        detail.transformed_item.add(item_id=102, item_tag=pb.EBagItemTag_Currency)
        # Field 100 is unknown to this client. Both copies must preserve it on the wire.
        raw = detail.SerializeToString() + bytes.fromhex("a0 06 01")
        db.conn.execute("INSERT INTO items_s VALUES(?,?,?)", (player, gear.instance_id, raw))
    treasure = pb.ItemDetail()
    item = treasure.main_item
    item.item_id, item.item_tag = 5070001, pb.EBagItemTag_TreasureMap
    item.treasure.item_id, item.treasure.instance_id, item.treasure.scene_id = 5070001, 95000, 1
    raw = treasure.SerializeToString()
    db.conn.execute("INSERT INTO items_s VALUES(?,?,?)", (player, 95000, raw))
    return raw


def performance(player, iterations):
    routines = [original_packets, PlayerMainData.inventory_notices]
    samples = [[], []]
    expected = serialized(original_packets(player))
    for step in range(iterations):
        for idx in (step % 2, 1 - step % 2):
            start = perf_counter_ns()
            packets = serialized(routines[idx](player))
            samples[idx].append((perf_counter_ns() - start) / 1_000_000)
            assert packets == expected
    peaks = []
    for routine in routines:
        tracemalloc.start()
        serialized(routine(player))
        peaks.append(tracemalloc.get_traced_memory()[1] // 1024)
        tracemalloc.stop()
    print(f"SQLite read through serialization, {iterations} runs each. Median ms "
          f"{median(samples[0]):.3f} -> {median(samples[1]):.3f}. "
          f"Peak traced Python KiB {peaks[0]} -> {peaks[1]} (native protobuf memory excluded).")


def run(root, resources, iterations):
    identity = LocalIdentity(root / "identity.sqlite3")
    profile = identity.create_profile("背包快照")
    service = LocalGameService(identity, root, resources, port=0)
    service.start()
    try:
        # Provision native starter tools before measuring the same snapshot on subsequent logins.
        first, _, _ = login(service.port, identity.issue_ticket(*profile))
        with first:
            game_inventory(first)
        with service.slot_available:
            assert service.slot_available.wait_for(lambda: not service.active, timeout=3)
        sock, _, player = login(service.port, identity.issue_ticket(*profile))
        with sock:
            assert not list(PlayerMainData.inventory_notices(player + 10000))
            expected = serialized(original_packets(player))
            assert serialized(PlayerMainData.inventory_notices(player)) == expected
            if iterations:
                print("Starter inventory")
                performance(player, iterations)
            with db.conn:
                obsolete = add_instances(player)
                # Deterministic choice permits byte comparison including an actual legacy update.
                with patch("private_treasure_dig.random.choice", side_effect=lambda seq: seq[0]):
                    expected = serialized(original_packets(player))
                    fixed = db.get_item_detail(player, instance_id=95000)
                    assert fixed != obsolete
                    db.conn.execute("UPDATE items_s SET item_detail_blob=? WHERE player_id=? AND instance_id=95000",
                                    (obsolete, player))
                    assert serialized(PlayerMainData.inventory_notices(player)) == expected
                    assert db.get_item_detail(player, instance_id=95000) == fixed
            queries = []
            db.conn.set_trace_callback(queries.append)
            try:
                assert serialized(PlayerMainData.inventory_notices(player)) == expected
            finally:
                db.conn.set_trace_callback(None)
            assert len(queries) == 1 and "UNION ALL" in queries[0], queries
            # A real login also sends temp storage; the regular bag is a consecutive packet sequence.
            frames = game_inventory(sock)
            assert any(frames[i:i + len(expected)] == expected for i in range(len(frames)))
            if iterations:
                print("Inventory with 93 alternating equipment instances and one restored treasure")
                performance(player, iterations)
                # Observe the whole request separately from inventory CPU and SQLite work.
                samples = [[], []]
                routines = [original_packets, PlayerMainData.inventory_notices]
                sock.close()
                for turn in range(min(11, iterations)):
                    for idx in (turn % 2, 1 - turn % 2):
                        with service.slot_available:
                            assert service.slot_available.wait_for(lambda: not service.active, timeout=3)
                        ticket = identity.issue_ticket(*profile)
                        client, _, _ = login(service.port, ticket)
                        with client, patch.object(PlayerMainData, "inventory_notices", routines[idx]):
                            start = perf_counter_ns()
                            game_inventory(client)
                            samples[idx].append((perf_counter_ns() - start) / 1_000_000)
                print(f"Loopback PlayerMainData request to scene notice median ms "
                      f"{median(samples[0]):.3f} -> {median(samples[1]):.3f}.")
            count = sum(len(pb.PackNotice.FromString(raw).items) for raw in expected)
            print(f"PASS {count} items, {len(expected)} ordered original-client packets, legacy repair, "
                  "unknown fields, nested gear, one read, no writes on repeat, real TCP login.")
    finally:
        service.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--benchmark", type=int, default=0, metavar="ITERATIONS")
    args = parser.parse_args()
    with TemporaryDirectory() as root:
        run(Path(root), args.resources, max(0, args.benchmark))
