"""National two-place vehicle prefab seats over original game frames, including old save UIDs."""
import argparse
from contextlib import ExitStack
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile("载具" + str(n)) for n in range(4)]
    service = LocalGameService(identity, root, resources, port=0)
    sequence = count(30)
    def ask(sock, name, **kwargs):
        return call(sock, name, next(sequence), **kwargs)
    def enter(sock, channel=1):
        assert ask(sock, "ChangeSceneChannel", scene_id=9999, channel_label=channel).status == pb.StatusCode_OK
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data
    def seats(snapshot):
        return {v.chair_id:dict(v.seat_info) for v in snapshot.vehicle_info_list}
    def ride(sock, owner, place, on=True):
        return ask(sock, "SceneSitVehicle", chair_id=owner, seat_id=place, is_sit=on)
    def notified(sock, uid, owner, seat, sitting):
        rsp = next_msg(sock, MsgId.SceneSitVehicleRsp, pb.SceneSitVehicleRsp)
        assert (rsp.player_id, rsp.chair_id, rsp.seat_id, rsp.is_sit) == (uid, owner, seat, sitting), rsp
    def equip(sock, key):
        rsp = ask(sock, "UpdateCharacterAppearance", char_id=101001, appearance=pb.CharacterAppearance(vehicle_instance_id=key))
        assert rsp.status == pb.StatusCode_OK
    service.start()
    try:
        with service.lock, db.conn:
            for pid,(slot,_) in enumerate(profiles[:2], 1):
                db.conn.execute("INSERT INTO private_slots(player_id,slot) VALUES(?,?)", (pid,slot))
                db.init_player(pid)
                db.conn.execute("UPDATE players SET player_name=? WHERE player_id=?", (identity.profile_name(slot),pid))
        service.stop()
        service.start()
        with ExitStack() as group:
            login_info = []
            for i, profile in enumerate(profiles):
                sock, _, uid = login(service.port, identity.issue_ticket(*profile))
                group.enter_context(sock)
                join(sock)
                enter(sock, 1 if i < 3 else 2)
                login_info.append((sock,uid))
            (a, aid),(b, bid),(c, cid),(d, did) = login_info
            assert aid >= 1000001 and bid >= 1000001
            assert ride(b, aid, 1).status == pb.StatusCode_PlayerChairRemoved
            with service.lock, db.conn:
                keys = []
                for tid in (1009004, 1009001):
                    item = make_item(tid, 1, 1)
                    key = item.main_item.weapon.instance_id
                    db.set_item_detail(1, item.SerializeToString(), instance_id=key)
                    keys.append(key)
            equip(a, keys[0])
            assert ride(b, aid, 0).status == pb.StatusCode_PlayerChairRemoved
            equip(a, keys[1])
            assert ride(b, 1, 1).status == pb.StatusCode_PlayerChairRemoved
            for seat in (-1, 2):
                assert ride(b, aid, seat).status == pb.StatusCode_BAD_REQ
            assert ride(d, aid, 0).status == pb.StatusCode_PlayerChairRemoved
            rsp = ride(b, aid, 1)
            assert (rsp.status,rsp.player_id,rsp.chair_id,rsp.seat_id,rsp.is_sit) == (pb.StatusCode_OK,bid,aid,1,True)
            notified(a, bid, aid, 1, True)
            notified(c, bid, aid, 1, True)
            assert ride(b, aid, 1).status == pb.StatusCode_OK
            assert ride(b, aid, 0).status == pb.StatusCode_PlayerSitOtherChair
            assert ride(c, aid, 1).status == pb.StatusCode_ChairSeatTaken
            assert ask(b, "SceneSitChair", chair_id=1, seat_id=0, is_sit=True).status == pb.StatusCode_PlayerSitOtherChair
            assert ride(c, aid, 0).status == pb.StatusCode_OK
            assert seats(enter(d, 2)) == {}
            late = enter(d)
            assert seats(late) == {aid:{0:cid, 1:bid}}
            assert [(p.player_id, p.vehicle_item_id) for p in late.players if p.vehicle_item_id] == [(aid,1009001)]
            rsp = ride(b, aid, 0, False)
            assert rsp.status == pb.StatusCode_OK and rsp.seat_id == 1
            notified(d, bid, aid, 1, False)
            enter(c, 2)
            notified(d, cid, aid, 0, False)
            assert seats(enter(d)) == {}
            assert ride(b, aid, 0).status == pb.StatusCode_OK
            notified(d, bid, aid, 0, True)
            equip(a, 0)
            notified(d, bid, aid, 0, False)
            assert seats(enter(d)) == {}
            equip(a, keys[1])
            assert ride(b, aid, 1).status == pb.StatusCode_OK
            notified(d, bid, aid, 1, True)
            a.close()
            notified(d, bid, aid, 1, False)
            assert seats(enter(d)) == {}
            assert ride(b, aid, 1).status == pb.StatusCode_PlayerChairRemoved
    finally:
        service.stop()
    print("PASS migrated vehicle owner and passenger UIDs, two seats, non-passenger prefab, foreign room, "
          "snapshots, occupied place, zero-seat dismount, passenger departure, unequip and owner disconnect")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),p.parse_args().resources)
