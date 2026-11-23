"""Exercise real national crafting, original inventory and handbook requests on two native sockets."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_observed_initialization import call


def request(sock, name, packet, **fields):
    entry = getattr(pb, name + "Req")(**fields)
    sock.sendall(encode(getattr(MsgId, name + "Req"), entry.SerializeToString(), packet))
    while True:
        head, body = receive(sock)
        if head.packet_id == packet and head.msg_id == getattr(MsgId, name + "Rsp"):
            return getattr(pb, name + "Rsp").FromString(body)


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    accounts = [identity.create_profile(n) for n in ("手工一", "手工二")]
    game = LocalGameService(identity, root, resources, port=0)
    def connect(i):
        sock, _, pid = login(game.port, identity.issue_ticket(*accounts[i]))
        join(sock)
        return sock, pid
    def seed(pid, costs):
        from utils import db
        from utils.pb_create import make_item
        with game.lock, db.conn:
            for template, count in costs.items():
                db.set_item_detail(pid, make_item(template, count, pid).SerializeToString(), template)
    def count(pid, item):
        from private_shop import balance
        with game.lock:
            record = balance(pid, item)
            return record.main_item.base_item.num if record else 0
    def gallery(sock, kind):
        response = call(sock, "GetOneTypeLifePictorialBookCount", life_type=kind)
        assert response.status == pb.StatusCode_OK
        return {r.id:r.count for r in response.life_achieve_count_lst}
    def handi(s, packet):
        return request(s, "Handicraft", packet, handicraft_id=5052001)
    def make(s, packet):
        return request(s, "MakeProp", packet, make_id=5045001)
    def reward(s, kind, aid):
        return call(s, "GetLifeAchievementReward", life_type=kind, achieve_id=aid)
    game.start()
    try:
        a, owner = connect(0)
        b, other = connect(1)
        with a, b:
            ok = pb.StatusCode_OK
            assert not gallery(a, 3) and not gallery(a, 4)
            sewing=pb.LIFE_TYPE_Sewing
            assert not gallery(a,sewing)
            # Seed only the exact fabric recipe. The two real accounts must keep separate wardrobes.
            seed(owner,{5031001:6,11:2})
            with patch("private_crafts.random.random", return_value=0.01):
                sewn=request(a,"Sewing",304,sewing_id=5032001)
                assert sewn.status==ok and sewn.life_common_result.result_items[0].main_item.item_id==5032001
                assert request(a,"Sewing",304,sewing_id=5032001)==sewn
            assert count(owner,5031001)==3 and gallery(a,sewing)=={5032001:1} and not gallery(b,sewing)
            assert call(a, "GetLifeInfo", life_type=0).status == pb.StatusCode_BAD_REQ
            assert call(a, "LifeLeveUp", life_type=3).status == pb.StatusCode_ProficiencyNotEnough
            assert request(a, "MakeProp", 90, make_id=0xffffffff).status == pb.StatusCode_CFG_NOT_FOUND
            assert request(a, "Handicraft", 91, handicraft_id=6018).status == pb.StatusCode_MODULE_CLOSE
            assert reward(a, 4, 150520011).status != ok
            seed(owner, {11:0, 3001:20, 5031106:12})
            assert handi(a, 92).status == pb.StatusCode_EnergyNotEnough
            assert count(owner, 5031106) == 12
            seed(owner, {11:100, 3001:0})
            assert handi(a, 93).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert count(owner, 11) == 100 and count(owner, 5031106) == 12
            seed(owner, {3001:20})
            with patch("private_crafts.random.random", return_value=0.99):
                fail = handi(a, 94)
                assert fail.status == ok and not fail.life_common_result.is_success and not fail.life_common_result.result_items
                assert handi(a, 94) == fail
            assert not gallery(a, 4) and count(owner, 11) == 80 and count(owner, 5031106) == 9
            original = count(owner, 5052001)
            with patch("private_crafts.random.random", return_value=0.1):
                for i in (95, 96, 97):
                    made = handi(a, i)
                    assert made.status == ok and made.life_common_result.is_success and made.life_common_result.life_type == 4
                    assert made.life_common_result.result_items[0].main_item.item_id == 5052001
                    assert handi(a, i) == made
            assert gallery(a, 4) == {5052001:3} and count(owner, 5052001) == original+3
            assert count(owner, 11) == 20 and count(owner, 5031106) == 0 and count(owner, 3001) == 0
            assert call(a,"GetLifeInfo",life_type=4).life_base_info.proficiency_value == 1800
            assert reward(a, 3, 150520011).status != ok
            claimed = reward(a, 4, 150520011)
            assert claimed.status == ok and list(claimed.rewarded_achieve_ids) == [150520011], claimed
            assert {r.main_item.item_id:r.main_item.base_item.num for r in claimed.items} == {102:5,130100101:1}
            assert reward(a, 4, 150520011).status == pb.StatusCode_REPEAT_GET_REWARD
            seed(owner, {11:1000, 5041001:0, 20300002:9})
            assert request(a,"MakeProp",100,make_id=5041001).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert count(owner,11) == 1000 and count(owner,20300002) == 9
            seed(owner, {20200002:40, 5052010:60, 5054001:60, 101:999})
            for i in range(101, 121):
                result = make(a,i)
                assert result.status == ok and result.life_common_result.result_items[0].main_item.item_id == 5045001
            assert gallery(a, 3) == {5045001:20}
            assert call(a, "GetLifeInfo", life_type=3).life_base_info.proficiency_value == 10000
            assert call(a, "LifeLeveUp", life_type=3).status == pb.StatusCode_ITEM_NOT_ENOUGH
            seed(owner,{101:1000})
            level = call(a,"LifeLeveUp",life_type=3)
            assert level.status == ok and level.life_base_info.level == 2 and level.life_base_info.proficiency_value == 0
            assert count(owner,101) == 0
            assert reward(a,3,150450011).status == ok
            assert not gallery(b,3) and not gallery(b,4) and reward(b,4,150520011).status != ok
        game.stop()
        game.start()
        a, _ = connect(0)
        b, _ = connect(1)
        with a, b:
            assert gallery(a,3) == {5045001:20} and gallery(a,4) == {5052001:3}
            assert reward(a,3,150450011).status == pb.StatusCode_REPEAT_GET_REWARD
            saved = call(a,"GetLifeAchieve",life_type=4).life_achieve
            assert 150520011 in saved.rewarded_life_achieve_id_lst
            assert any(x.achieve_id == 150520011 and x.count == 3 for x in saved.life_achieve_)
            assert call(a,"GetLifeInfo",life_type=3).life_base_info.level == 2
            assert not gallery(b,3) and not gallery(b,4)
            assert not call(b,"GetLifeAchieve",life_type=4).life_achieve.rewarded_life_achieve_id_lst
    finally:
        game.stop()
    print("PASS native PC furniture/handicraft: configured recipes, energy, ingredients, failure and retry, proficiency, level upgrades, per-role gallery and one-time rewards across restart.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as scratch:
        run(Path(scratch),parser.parse_args().resources)
