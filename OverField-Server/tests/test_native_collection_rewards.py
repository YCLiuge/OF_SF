"""Collect then claim each configured field journal reward through native client messages."""
import argparse
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_collection_rewards import region, reward_pool
from private_dungeons import _table, progress
from test_game_realm import join, login
from test_native_gacha import call
from utils import db


def achievement_scenarios(sock, pid, catalog):
    def move(scene):
        assert call(sock,"ChangeSceneChannel",70,scene_id=scene).status==pb.StatusCode_OK
    def pick(item):
        assert call(sock,"Collecting",71,item_id=item).status==pb.StatusCode_OK
    move(1)
    for item in range(10101001,10101005):
        pick(item)
    assert progress(pid,50101020)==progress(pid,50101021)==4
    assert progress(pid,55101020)==0
    assert call(sock,"Collecting",72,item_id=10101001).status==pb.StatusCode_ALREADY_COLLECTED
    assert call(sock,"CollectionReward",73,item_id=10101001).status==pb.StatusCode_OK
    assert progress(pid,50101021)==4
    for kind, aid in ((2,50101090),(3,50101050),(4,50101040),(5,50101110),
                      (6,50101030),(7,50101080),(8,50101060)):
        pick(next(item for item,row in catalog.items() if row["new_collection_type"]==kind and region(item)==1))
        assert progress(pid,aid)==1, (kind,aid)
    assert progress(pid,60000227)==1
    pick(10901001)
    pick(11001001)
    assert progress(pid,50101060)==1 and progress(pid,50101080)==1
    move(1001)
    pick(10151001)
    assert progress(pid,55101020)==1 and progress(pid,50101021)==4
    move(2)
    pick(10502001)
    assert progress(pid,60000227)==2 and progress(pid,50101110)==1
    exact = [(r["i_d"],r["param"][0]) for r in _table("Achieve","achieve").values()
             if r.get("new_achieve_condition")==2 and r["param"][0] in catalog]
    assert len(exact)==13
    for aid,item in exact:
        move(region(item))
        pick(item)
        assert progress(pid,aid)==1


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    slots = [identity.create_profile(n) for n in ("收藏甲", "收藏乙", "收藏旧档")]
    host = LocalGameService(identity, root, resources, port=0)

    def enter(index):
        sock, _, pid = login(host.port, identity.issue_ticket(*slots[index]))
        _, scene = join(sock)
        return sock, pid, scene

    def amount(pid):
        with host.lock:
            return pb.ItemDetail.FromString(db.get_item_detail(pid, 102)).main_item.base_item.num

    def claim(sock, cid):
        return call(sock, "CollectionReward", 15, item_id=cid)

    host.start()
    try:
        a, pid, _ = enter(0)
        b, peer, _ = enter(1)
        old, legacy, _ = enter(2)
        with a, b, old:
            catalog = _table("CollectionItem", "collection_item")
            groups = defaultdict(list)
            for cid, config in catalog.items():
                if reward_pool(config):
                    groups[region(cid)].append(cid)
            total = sum(map(len, groups.values()))
            assert total == 915
            start = amount(pid)
            assert claim(a, 10101001).status == pb.StatusCode_NOT_COLLECTED
            assert call(a, "Collecting", 21, item_id=10101001).status == pb.StatusCode_BAD_REQ
            for scene, ids in sorted(groups.items()):
                assert call(a, "ChangeSceneChannel", 22, scene_id=scene).status == pb.StatusCode_OK, scene
                for cid in ids:
                    kind = catalog[cid]["new_collection_type"]
                    result = call(a, "Collecting", 23, item_id=cid)
                    assert result.status == pb.StatusCode_OK, (scene,cid,result)
                    group, = result.collections
                    assert group.type == kind and group.level >= 1 and group.item_map[cid].status == pb.NotReward
                    result = claim(a, cid)
                    assert result.status == pb.StatusCode_OK and result.collection_reward_data.status == pb.Reward, cid
                    assert [(x.main_item.item_id, x.main_item.base_item.num) for x in result.items] == [(102,10)]
                assert call(a, "Collecting", 23, item_id=ids[0]).status == pb.StatusCode_ALREADY_COLLECTED
                assert claim(a, ids[0]).status == pb.StatusCode_COLLECTION_ALREADY_REWARDED
            assert amount(pid) == start + total * 10
            assert claim(b, 10101001).status == pb.StatusCode_NOT_COLLECTED
            assert call(a, "ChangeSceneChannel", 25, scene_id=1).status == pb.StatusCode_OK
            achievement_scenarios(b,peer,catalog)
            before_nonpaying = amount(pid)
            for kind in (4, 5, 7, 10, 11):
                cid = next(key for key, row in catalog.items() if row["new_collection_type"] == kind and region(key) == 1)
                picked = call(a, "Collecting", 27, item_id=cid)
                assert picked.status == pb.StatusCode_OK and picked.collections[0].item_map[cid].status == pb.NotReward
                marked = claim(a, cid)
                assert marked.status == pb.StatusCode_OK and marked.collection_reward_data.status == pb.Reward and not marked.items
                assert claim(a, cid).status == pb.StatusCode_COLLECTION_ALREADY_REWARDED
            assert amount(pid) == before_nonpaying
            picked = call(a, "Collecting", 26, item_id=10901001)
            assert picked.status == pb.StatusCode_OK and picked.collections[0].item_map[10901001].status == pb.Reward
            assert claim(a, 10901001).status == pb.StatusCode_COLLECTION_ALREADY_REWARDED
            with host.lock, db.conn:
                db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='collection_rewards'", (legacy,))
                db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='field_collection_achievements'", (legacy,))
                for item in (10101001,10101002,10101003,10101004,10151001,10202022,10801001):
                    kind=catalog[item]["new_collection_type"]
                    row=pb.PBCollectionRewardData(item_id=item,status=pb.Reward)
                    db.set_collection(legacy,item,kind,row.SerializeToString())
                for item in (10100001, 10100002):
                    assert reward_pool(catalog[item])
                    row = pb.PBCollectionRewardData(item_id=item, status=pb.Reward)
                    db.set_collection(legacy, item, 1, row.SerializeToString())
                db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,'collection',?)", (legacy,"10100002"))
    finally:
        host.stop()
    host.start()
    try:
        old, legacy, scene = enter(2)
        with old:
            file_group = next(r for r in scene.data.collections if r.type == 1)
            assert file_group.item_map[10100001].status == pb.NotReward
            assert file_group.item_map[10100002].status == pb.Reward
            assert progress(legacy,50101021)==4 and progress(legacy,55101020)==1
            assert progress(legacy,60040101)==progress(legacy,50101060)==1
            start = amount(legacy)
            assert claim(old,10100001).status == pb.StatusCode_OK
            assert claim(old,10100002).status == pb.StatusCode_COLLECTION_ALREADY_REWARDED
            assert amount(legacy) == start + 10
            assert call(old,"ChangeSceneChannel",75,scene_id=1).status==pb.StatusCode_OK
            assert call(old,"Collecting",76,item_id=10101005).status==pb.StatusCode_OK
            assert progress(legacy,50101021)==5 and progress(legacy,55101020)==1
        a, pid, _ = enter(0)
        with a:
            assert claim(a,10101001).status == pb.StatusCode_COLLECTION_ALREADY_REWARDED
    finally:
        host.stop()
    print(f"PASS {total} journal discoveries and native reward claims, scene levels, eight field achievement types, exact targets, automatic pickups, legacy rows, isolation and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp), parser.parse_args().resources)
