"""Schedule national stage entrances, exercise native claims and restore the original calendar."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import realm_admin
from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN
from realm_maintenance import Maintenance
from test_game_realm import login
from test_native_events import rpc, toggled


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    slot = identity.create_profile("广场活动测试")
    game = LocalGameService(identity, root, resources, port=0)
    def owner(method, data=None):
        return realm_admin.execute(root, method, data or {}, lambda: None)
    def pulse(s, n):
        _, first = rpc(s, "PlayerPing", n)
        _, second = rpc(s, "PlayerPing", n+1)
        return first + second
    when = [datetime(2026,11,24,12,0,0,tzinfo=CN)]
    months = {"theme":23, "activities":[5087,260212103,260212104], "arcade":12003,
              "opens_at":"2026-11-24 12:00:05", "closes_at":"2026-11-24 12:00:10"}
    event = {"events":[{"id":4008,"enabled":False,"opens_at":None,"closes_at":None}]}
    assert len(owner("plaza.catalog")["themes"]) == 58
    assert owner("plaza.get") == {}
    owner("events.set", event)
    owner("plaza.set", months)
    assert owner("events.get") == event and owner("plaza.get") == months
    assert Maintenance(identity).plaza() == months
    for bad in ({**months,"theme":99}, {**months,"activities":[4008]}, {**months,"arcade":12004},
                {**months,"activities":[5075,260815130]}, {**months,"opens_at":months["closes_at"]}):
        try:
            owner("plaza.set",bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid plaza selection accepted")
    with patch("private_shop.now", side_effect=lambda:when[0]):
        game.start()
        try:
            sock, _, _ = login(game.port,identity.issue_ticket(*slot))
            with sock:
                _, notices = rpc(sock,"PlayerMainData",3)
                assert 260212103 not in toggled(notices + pulse(sock,4),True)
                when[0] = when[0].replace(second=5)
                assert set(months["activities"]) <= toggled(pulse(sock,6),True)
                assert rpc(sock,"ActivitySignIn",8,activity_id=260212103,day=1)[0].status == pb.StatusCode_OK
                assert rpc(sock,"ActivitySignIn",9,activity_id=260212103,day=1)[0].status == pb.StatusCode_REPEAT_GET_REWARD
                assert rpc(sock,"RestaurantGameInfo",10,activity_id=12003)[0].status == pb.StatusCode_OK
                assert rpc(sock,"RestaurantGameInfo",11,activity_id=12000)[0].status == pb.StatusCode_Activity_Close
                when[0] = when[0].replace(second=11)
                assert set(months["activities"]) <= toggled(pulse(sock,12),False)
                assert rpc(sock,"ActivitySignIn",14,activity_id=260212103,day=1)[0].status == pb.StatusCode_Activity_Close
                assert rpc(sock,"RestaurantGameInfo",15,activity_id=12000)[0].status == pb.StatusCode_OK
        finally:
            game.stop()
    assert owner("plaza.get") == months
    assert owner("plaza.set",{}) == {} and owner("events.get") == event
    print("PASS 58 plaza theme entries, seasonal selection, native sign-in rewards, original arcade, live windows, rollback and reset.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),cli.parse_args().resources)
