"""Two native clients check finite home limits, scene visibility, native points and durable furniture."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from realm_rules import configure
from test_game_realm import join, login, next_msg
from test_observed_initialization import call


def pending_furniture(sock):
    """Use an ordered ping reply as a fence for earlier updates and removals on this socket."""
    sock.sendall(encode(MsgId.PlayerPingReq, pb.PlayerPingReq(client_time_ms=5361).SerializeToString(), 91))
    changes = []
    while True:
        head, body = receive(sock)
        if head.msg_id == MsgId.PlayerPingRsp and head.packet_id == 91:
            return changes
        if head.msg_id == MsgId.SceneGardenFurnitureUpdateNotice:
            update = pb.SceneGardenFurnitureUpdateNotice.FromString(body)
            changes.append(("update", update.furniture_info.furniture_id))
        if head.msg_id == MsgId.SceneGardenFurnitureRemoveNotice:
            removal = pb.SceneGardenFurnitureRemoveNotice.FromString(body)
            changes.append(("remove", removal.furniture_id))


def run(state, resources, report):
    configure(state, {"home_furniture_limit":2})
    identity = LocalIdentity(state / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("核验屋主","核验访客")]
    service = LocalGameService(identity, state, resources, port=0)
    items = json.loads((resources / "data/PlazaMusic.json").read_bytes())["instruments"][:3]
    points = {r["item_i_d"]: r.get("home_item_point_num",0)
              for r in json.loads((resources / "data/HomeItem.json").read_bytes())["home_item"]["datas"]}
    expected = sum(points[i] for i in items)
    assert expected > len(items)

    def connect(index):
        sock, _, uid = login(service.port, identity.issue_ticket(*profiles[index]))
        _, notice = join(sock)
        return sock, uid, notice.data

    def switch(sock, channel):
        result = call(sock, "ChangeSceneChannel", channel_label=channel, scene_id=9999)
        assert result.status == pb.StatusCode_OK, result
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data

    def place(sock, template, fid=0, x=12):
        return call(sock, "PlaceFurniture", furniture_id=fid, furniture_item_id=template,
                    pos={"x":x,"z":32}, rot={"y":75}, layer_num=1)

    service.start()
    try:
        owner, home, _ = connect(0)
        guest, visitor, _ = connect(1)
        with owner, guest:
            assert home != visitor
            switch(owner, home)
            assert call(owner, "GetGardenInfo").garden_info.furniture_limit_num == 200000
            first = place(owner, items[0])
            assert first.status == pb.StatusCode_OK
            fid = first.furniture_details_info.furniture_id
            assert pending_furniture(guest) == []
            view = switch(guest, home)
            assert view.scene_garden_data.garden_furniture_info_map[fid].pos.x == 12
            assert view.scene_garden_data.furniture_player_map[fid] == home
            assert call(guest, "GetGardenInfo").garden_info.furniture_limit_num == 200000
            owner2 = place(owner, items[1])
            assert owner2.status == pb.StatusCode_OK
            second = owner2.furniture_details_info.furniture_id
            assert pending_furniture(guest) == [("update",second)]
            assert place(owner, items[2]).status != pb.StatusCode_OK
            assert pending_furniture(guest) == []
            updated = place(owner, items[0], fid, 37)
            assert updated.status == pb.StatusCode_OK and pending_furniture(guest) == [("update",fid)]
            assert pending_furniture(owner) == [("update", fid)]
            placed = place(guest, items[2])
            assert placed.status == pb.StatusCode_OK
            third = placed.furniture_details_info.furniture_id
            assert len({fid, second, third}) == 3 and pending_furniture(owner) == [("update",third)]
            assert place(guest, items[0]).status != pb.StatusCode_OK
            assert call(guest, "TakeOutFurniture", furniture_id=fid,is_put_in_package=True).status != pb.StatusCode_OK
            assert pending_furniture(owner) == []
            garden = switch(owner,home).scene_garden_data
            assert garden.furniture_current_point_num == expected, (garden.furniture_current_point_num, expected)
            assert garden.furniture_player_map == {fid:home, second:home, third:visitor}
            assert set(garden.other_player_furniture_info_map) == {third}
            assert garden.garden_furniture_info_map[fid].rotation.y == 75
            abroad = switch(guest, 2).scene_garden_data
            assert not abroad.garden_furniture_info_map
            public = place(guest, items[0])
            assert public.status == pb.StatusCode_OK
            public_id = public.furniture_details_info.furniture_id
            assert pending_furniture(owner) == []
            assert set(switch(owner,2).scene_garden_data.garden_furniture_info_map)=={public_id}
            switch(guest,home)
            assert pending_furniture(owner) == [("remove",public_id)]
            assert not switch(owner,2).scene_garden_data.garden_furniture_info_map
            assert switch(owner,home).scene_garden_data.furniture_current_point_num == expected
        service.stop()
        service.start()
        owner, again, restored = connect(0)
        with owner:
            assert again == home and restored.channel_id == home
            assert restored.scene_garden_data.furniture_current_point_num == expected
            assert restored.scene_garden_data.garden_furniture_info_map[fid].pos.x == 37
            assert restored.scene_garden_data.furniture_player_map[third] == visitor
            assert call(owner, "TakeOutFurniture", furniture_id=second,is_put_in_package=True).status == pb.StatusCode_OK
            assert switch(owner,home).scene_garden_data.furniture_current_point_num == expected - points[items[1]]
            if report:
                # Probe separate, explicitly unsupported UI actions in this disposable realm.
                probes = {"GardenFurnitureScheme":{}, "GardenFurnitureSave":{"scheme_id":1},
                          "GardenSchemeFurnitureList":{"scheme_id":1}, "GardenFurnitureApplyScheme":{"scheme_id":1},
                          "GardenFurnitureSchemeSetName":{"scheme_id":1,"name":"测试方案"},
                          "GardenFurnitureRemoveAll":{}, "GardenPlaceCharacter":{"character_id":101001,"furniture_id":fid},
                          "SetRoomDecor":{}, "DoLikes":{}, "GardenLikeRecord":{}}
                for name, values in probes.items():
                    request = getattr(pb, name+"Req")(**values)
                    owner.sendall(encode(getattr(MsgId,name+"Req"), request.SerializeToString(), 60))
                    rsp = next_msg(owner,getattr(MsgId,name+"Rsp"),getattr(pb,name+"Rsp"))
                    print(name,pb.StatusCode.Name(rsp.status))
    finally:
        service.stop()
    print(f"PASS owner limit=2, visitor=1, independent room admission, updates and removals, {expected} points and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--report", action="store_true", help="Report responses to optional garden operations.")
    args = parser.parse_args()
    with TemporaryDirectory() as scratch:
        run(Path(scratch),args.resources,args.report)
