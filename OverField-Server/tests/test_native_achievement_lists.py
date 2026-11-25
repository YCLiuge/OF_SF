"""Two native clients query authored area/character achievement IDs and independent saved progress."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from test_game_realm import join, login
from test_native_gacha import call
from utils import db


def run(root, resources):
    accounts = LocalIdentity(root / "identity.sqlite3")
    profiles = [accounts.create_profile(label) for label in ("成就甲","成就乙")]
    game = LocalGameService(accounts,root,resources,port=0)
    hero = 101001

    def connect(profile):
        sock, _, pid = login(game.port, accounts.issue_ticket(*profile))
        join(sock)
        return sock, pid

    def character(sock,char=hero):
        return call(sock,"GetCharacterAchievementList",20,character_id=char)

    def area(sock,device):
        return call(sock,"AreaAchieveList",21,area_id=device)

    def values(rows):
        return {v.achieve_id:v.count for v in rows}

    game.start()
    try:
        a, pid = connect(profiles[0])
        b, peer = connect(profiles[1])
        with a, b:
            first = character(a)
            initial = values(first.character_achievement_lst)
            assert first.status == pb.StatusCode_OK and len(initial) == 8
            assert initial[41010014] == 1 and initial[41010016] == initial[41010010] == 0
            assert character(a,0).status == character(b,987654321).status == pb.StatusCode_CharNotExist
            assert not area(a,101).achieves and area(a,101).status == pb.StatusCode_OK
            assert area(a,987654321).status == pb.StatusCode_BAD_REQ
            default = area(a,1001)
            assert default.status==pb.StatusCode_OK and values(default.achieves)=={10111:0,10112:0,10113:0}
            # Historical facts for a single role, including old-community payout receipts.
            with game.lock, db.conn:
                char = pb.Character.FromString(db.get_characters(pid,hero)[0])
                char.level, char.max_level, char.star = 47, 60, 2
                char.reward_index_lst.append(1)
                char.rewarded_achievement_id_lst.append(41010014)
                char.is_unlock_payment=True
                db.set_character(pid,hero,char.SerializeToString())
                for ns, key in (("chr_achieve",f"{hero}:2"),("chr_badge",f"{hero}:41010010")):
                    db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",(pid,ns,key))
                for aid,n in ((41010016,7),(41010017,3),(10121,15)):
                    db.set_achieve(pid,aid,pb.Achieve(achieve_id=aid,count=n).SerializeToString())
                # One real document belongs to this area. An older scene-wide total of
                # fifteen cannot stand in for the two other documents at this device.
                doc = pb.PBCollectionRewardData(item_id=10101015, status=pb.NotReward)
                db.set_collection(pid,doc.item_id,1,doc.SerializeToString())
                for scene,device,tier in ((1,1001,2),(3,3001,2),(6,3001,1)):
                    db.set_area(pid,scene,device,pb.AreaData(area_id=device,level=tier).SerializeToString())
            got=character(a)
            progress=values(got.character_achievement_lst)
            assert progress=={41010010:5,41010011:2,41010012:47,41010013:2,
                              41010014:20,41010015:40,41010016:7,41010017:3}
            assert list(got.has_rewarded_ids)==[1,2] and list(got.rewarded_id_lst)==[41010010,41010014]
            assert got.is_unlocked_payment and not first.is_unlocked_payment
            assert values(character(b).character_achievement_lst)==initial
            assert not character(b).has_rewarded_ids and not character(b).rewarded_id_lst
            stage2=values(area(a,1001).achieves)
            assert stage2=={10121:1,10122:0,10123:0}
            assert area(b,1001)==default
            assert area(a,3001).status==pb.StatusCode_BAD_REQ
            assert call(a,"ChangeSceneChannel",22,scene_id=3).status==pb.StatusCode_OK
            assert area(a,3001).status==pb.StatusCode_OK and area(a,3001).achieves
            assert area(b,3001).status==pb.StatusCode_OK
            with game.lock:
                unchanged=db.get_characters(pid,hero)[0]
            for _ in range(2):
                assert character(a)==got
            with game.lock:
                assert unchanged==db.get_characters(pid,hero)[0]
    finally:
        game.stop()
    game.start()
    try:
        a, _=connect(profiles[0])
        b, _=connect(profiles[1])
        with a,b:
            assert character(a)==got and values(area(a,1001).achieves)==stage2
            assert values(character(b).character_achievement_lst)==initial and area(b,1001)==default
    finally:
        game.stop()
    print("PASS national area tiers/conditions, legitimate empty guide device, saved character facts/receipts, two roles and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
