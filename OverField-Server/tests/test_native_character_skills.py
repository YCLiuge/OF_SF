"""Run PC character training requests against two isolated profiles and a restarted native realm."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


def run(root, resources):
    ident = LocalIdentity(root / "identity.sqlite3")
    a, b = [ident.create_profile(label) for label in ("探索员甲","探索员乙")]
    game = LocalGameService(ident, root, resources, port=0)
    cid, normal, ultimate = 101001, 1010700, 1010725

    def connect(profile):
        sock, _, player = login(game.port, ident.issue_ticket(*profile))
        main, _ = join(sock)
        return sock, player, {c.character_id:c for c in main.characters}

    def own(pid):
        return pb.Character.FromString(db.get_characters(pid,cid)[0])

    def levels(c):
        return {s.skill_id:s.skill_level for s in c.character_skill_list}

    def amount(pid, item):
        raw = db.get_item_detail(pid,item)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0

    def fixture(pid, amounts):
        with game.lock, db.conn:
            for item, count in amounts.items():
                db.set_item_detail(pid, make_item(item,count,pid).SerializeToString(),item)

    def upgrade(sock, packet, sid, char=cid):
        return call(sock, "CharacterSkillLevelUp", packet, char_id=char, skill_id=sid)

    def xp(sock, packet, drinks, char=cid):
        return call(sock,"CharacterLevelUp",packet,char_id=char,nums=drinks)

    def ascend(sock, packet):
        return call(sock,"CharacterLevelBreak",packet,char_id=cid)

    def balances(items):
        return {d.main_item.item_id:d.main_item.base_item.num for d in items}

    game.start()
    try:
        s, p, _ = connect(a)
        t, q, _ = connect(b)
        with s, t:
            table = res["CharacterSkills"]
            assert len(table["steps"]) == 88 and len(table["costs"]) == 18
            assert sum(any(entries) for entries in table["steps"].values()) == 87
            assert all(len(entries) == 10 and not entries[-1] for entries in table["steps"].values())
            fixture(p,{101:0,1101:2,190:3,191:0,192:0})
            assert upgrade(s,30,normal).status == pb.StatusCode_ITEM_NOT_ENOUGH
            assert amount(p,1101) == 2 and levels(own(p))[normal] == 1
            assert upgrade(s,31,1).status == pb.StatusCode_BAD_REQ
            assert upgrade(s,32,normal,0).status == pb.StatusCode_CharNotExist
            with game.lock, db.conn:
                if not db.get_characters(p,301003):
                    db.add_character(p,301003)
            assert upgrade(s,33,3010505,301003).status == pb.StatusCode_BAD_REQ
            assert xp(s,34,[1]).status == pb.StatusCode_ITEM_NOT_ENOUGH and amount(p,190) == 3
            assert xp(s,35,[1,1,0]).status == pb.StatusCode_ITEM_NOT_ENOUGH and amount(p,190) == 3
            assert xp(s,36,[1],0).status == pb.StatusCode_CharNotExist and amount(p,190) == 3
            for invalid in ([],[0],[-1],[1,0,0,0]):
                assert xp(s,37,invalid).status == pb.StatusCode_BAD_REQ
            fixture(p,{101:500000,1101:100,1102:100,1103:100,1104:100,1201:100,1202:100,1203:100,1204:100})
            first = upgrade(s,40,normal)
            assert (first.skill.skill_id,first.skill.skill_level,first.char_id) == (normal,2,cid)
            assert balances(first.items) == {1101:99,101:498750}
            h, raw = receive(s)
            assert h.msg_id == MsgId.PackNotice and h.packet_id == 0
            assert balances(pb.PackNotice.FromString(raw).items) == balances(first.items)
            assert upgrade(s,40,normal) == first and amount(p,101) == 498750
            assert own(p).level == 1 and own(p).star == 0
            for sid, begin, base in ((normal,2,10000),(ultimate,1,30000)):
                for current in range(begin,10):
                    price = {int(k):v for k,v in table["costs"][str(base+current)].items()}
                    before = {k:amount(p,k) for k in price}
                    result = upgrade(s,base+current,sid)
                    assert result.status == pb.StatusCode_OK and result.skill.skill_level == current+1
                    assert all(amount(p,k)==before[k]-cost for k,cost in price.items())
                before = amount(p,101)
                assert upgrade(s,base+15,sid).status == pb.StatusCode_CharacterSkillLvIsMax
                assert amount(p,101)==before
            assert all(v==1 for v in levels(own(q)).values())
            old_coins = amount(p,101)
            changed = xp(s,60,[2])
            assert (changed.level, changed.exp) == (2,1000) and amount(p,101) == old_coins-40
            assert xp(s,60,[2]) == changed and amount(p,190) == 1
            assert ascend(s,61).status == pb.StatusCode_BAD_REQ
            # Stage a migrated character at 60, then exercise the retail 60 -> 70 cap and real costs.
            with game.lock, db.conn:
                char = own(p)
                char.level = char.max_level = 60
                char.exp = 0
                db.set_character(p,cid,char.SerializeToString())
            fixture(p,{101:0,1002:4})
            assert ascend(s,62).status == pb.StatusCode_ITEM_NOT_ENOUGH and amount(p,1002) == 4
            assert xp(s,63,[1]).status == pb.StatusCode_BAD_REQ and amount(p,190) == 1
            fixture(p,{101:6000})
            result = ascend(s,64)
            assert (result.level,result.max_level,result.exp)==(60,70,0) and amount(p,101)==0
            assert amount(p,1002)==0 and ascend(s,64)==result
            with game.lock, db.conn:
                db.conn.execute("INSERT INTO player_kv VALUES(?,'skills',?,?)",(q,f"{cid}:{normal}",b"4"))
                db.conn.execute("INSERT INTO player_kv VALUES(?,'skills',?,?)",(q,f"{cid}:{ultimate}",b"200"))
    finally:
        game.stop()
    game.start()
    try:
        s, _, roster = connect(a)
        t, _, other = connect(b)
        with s, t:
            assert levels(roster[cid])[normal] == levels(roster[cid])[ultimate] == 10
            assert (roster[cid].level,roster[cid].max_level) == (60,70)
            assert levels(other[cid])[normal] == 4 and levels(other[cid])[ultimate] == 1
            assert amount(p,101)==0 and amount(p,1002)==0
    finally:
        game.stop()
    print("PASS 18 retail skill tiers, level-10 caps, special skill exclusion, two profiles, skill/XP/break atomic payments, same-packet replay, migration and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    with TemporaryDirectory() as state:
        run(Path(state), parser.parse_args().resources)
