"""Schedule national stage entrances, exercise native claims and restore the original calendar."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import realm_admin
from local_game_service import LocalGameService, MsgId, pb
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
                # Independently rerun the original festival without disturbing the operator's other events or plaza.
                sakura={"id":5082,"enabled":True,"opens_at":"2026-11-24 00:00:00","closes_at":"2026-11-30 23:59:59"}
                owner("events.set",{"events":[*event["events"],sakura]})
                assert owner("plaza.get")==months
                from local_game_wire import encode,receive
                from test_game_realm import next_msg
                from private_fireworks import schedule
                import json
                raw=json.dumps({"events":[sakura]})
                def check(hour,minute,day=24):
                    return schedule(when[0].replace(day=day,hour=hour,minute=minute,second=0),raw)
                assert check(19,59) is None and check(20,10) is None and check(20,0,23) is None
                assert schedule(datetime(2026,12,1,20,0,tzinfo=CN),raw) is None
                sock.sendall(encode(MsgId.ChangeSceneChannelReq,pb.ChangeSceneChannelReq(scene_id=9999,channel_label=1).SerializeToString(),29))
                assert not next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data.HasField("fireworks_info")
                when[0]=when[0].replace(hour=20,minute=2,second=0)
                sock.sendall(encode(MsgId.PlayerPingReq,b"",30))
                first=next_msg(sock,MsgId.FireworksStartNotice,pb.FireworksStartNotice)
                assert first.fireworks_info==check(20,2)
                assert first.fireworks_info.fireworks_duration_time==600
                for packet in (31,32):
                    sock.sendall(encode(MsgId.PlayerPingReq,b"",packet))
                    while True:
                        head,_=receive(sock)
                        assert head.msg_id!=MsgId.FireworksStartNotice
                        if head.msg_id==MsgId.PlayerPingRsp and head.packet_id==packet:break
                sock.sendall(encode(MsgId.ChangeSceneChannelReq,pb.ChangeSceneChannelReq(scene_id=9999,channel_label=2).SerializeToString(),33))
                snapshot=next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
                assert snapshot.fireworks_info==first.fireworks_info and snapshot.tod_time==20*3600+120*50
                owner("events.set",event)
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
