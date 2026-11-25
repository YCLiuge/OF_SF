"""Optional acceptance against an operator's OF_SF TLS gateway using a fresh QA character."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets

from local_game_wire import encode, receive
from network.msg_id import MsgId
from player_tls_channels import PlayerTlsChannels
from proto import net_pb2 as pb
from remote_password import RemotePassword
from test_game_realm import next_msg
from test_native_gacha import call


def account(channels, action, body, expected=200):
    with channels.open(18881) as stream, closing(HTTPConnection("127.0.0.1",18881,timeout=12)) as http:
        http.sock = stream
        http.request("POST","/local/v1/accounts/"+action,json.dumps(body).encode(),
                     {"Content-Type":"application/json","Connection":"close"})
        response = http.getresponse()
        assert response.status==expected, (action,response.status)
        return json.loads(response.read(8192))


@contextmanager
def resident(channels, credentials):
    auth = account(channels,"login",credentials)
    signed = RemotePassword(None,channels,18881,11033).reserve(auth["slot"],auth["ticket"])
    with channels.open(11033) as sock:
        assert call(sock,"VerifyLoginToken",1,login_token=signed.ticket).status==pb.StatusCode_OK
        assert call(sock,"PlayerLogin",2).status==pb.StatusCode_OK
        yield sock
        assert call(sock,"PlayerOffline",999).status==pb.StatusCode_OK


def add_item(totals,info):
    kind = info.WhichOneof("item")
    if kind in ("armor","weapon"):
        totals[info.item_id,getattr(info,kind).instance_id] += 1
    elif kind=="base_item":
        totals[info.item_id,0] += info.base_item.num


def snapshot(sock):
    sock.sendall(encode(MsgId.PlayerMainDataReq,pb.PlayerMainDataReq().SerializeToString(),3))
    main = next_msg(sock,MsgId.PlayerMainDataRsp,pb.PlayerMainDataRsp)
    assert main.status==pb.StatusCode_OK
    inventory = Counter()
    for _ in range(1024):
        h,blob = receive(sock)
        if h.msg_id==MsgId.PackNotice:
            for item in pb.PackNotice.FromString(blob).items:
                if item.pack_type==pb.ItemDetail.PackType_Inventory:
                    add_item(inventory,item.main_item)
        if h.msg_id==MsgId.SceneDataNotice:
            scene=pb.SceneDataNotice.FromString(blob).data
            assert scene.scene_id==main.scene_id
            return scene,inventory
    raise AssertionError("No native scene snapshot")


def check(sock,name,packet,**fields):
    reply=call(sock,name,packet,**fields)
    assert reply.status==pb.StatusCode_OK,(name,packet,reply.status)
    return reply


def new_character(channels):
    nonce=secrets.token_hex(4)
    credentials={"username":"qaloot"+nonce,"password":secrets.token_urlsafe(24)}
    account(channels,"register",{**credentials,"nickname":"拾取"+nonce[:6]},201)
    return credentials


def preview(sock,resources):
    nodes=json.loads((resources/"data"/"EncounterMonsterNodes.json").read_bytes())["encounters"]
    check(sock,"ChangeSceneChannel",4,scene_id=1,channel_label=1)
    scene=next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data
    assert scene.scene_id==1
    ids=sorted(int(k) for k in nodes if int(k)//1000==1)
    entries=check(sock,"BattleEncounterInfo",10,encounter_ids=ids).encounters
    marked=[e.battle_id for e in entries if e.state==pb.Start and e.box_id==300000]
    if not marked:
        raise RuntimeError(f"No marked encounter among {len(ids)}. Check the realm's configured chance.")
    chosen=marked[0]
    verify=check(sock,"BattleEncounterInfo",11,encounter_ids=[chosen])
    assert verify.encounters[0].box_id==300000
    return chosen,nodes[str(chosen)],len(marked),len(ids)


def run(address,ca,resources):
    channels=PlayerTlsChannels(address,str(ca))
    credentials=new_character(channels)
    with resident(channels,credentials) as sock:
        snapshot(sock)
        key,units,marked,total=preview(sock,resources)
    with resident(channels,credentials) as sock:
        scene,before=snapshot(sock)
        assert scene.scene_id==1
        verify=check(sock,"BattleEncounterInfo",5,encounter_ids=[key])
        assert verify.encounters[0].box_id==300000
        start=check(sock,"BattleEncounterStateUpdate",6,encounter_id=key,battle_state=pb.Start)
        assert start.encounter.box_id==300000
        for packet,(unit,index) in enumerate(units,10):
            died=check(sock,"MonsterDead",packet,monster_id=unit,monster_index=index)
            assert not died.HasField("drop_item")
        won=check(sock,"BattleEncounterStateUpdate",30,encounter_id=key,battle_state=pb.Finish)
        box=won.dynamic_treasure_box_base_info
        index=box.index
        assert index and box.box_id==won.encounter.box_id==300000
        assert check(sock,"BattleEncounterStateUpdate",31,encounter_id=key,battle_state=pb.Finish)==won
        opened=check(sock,"TreasureBoxOpen",32,box_type=1,treasure_box_index=index,loc_type=0)
        assert opened.items
        expected=Counter()
        actual=Counter()
        to_store=Counter()
        for item in opened.items:add_item(expected,item.main_item)
        first=check(sock,"TreasureBoxPickup",33,box_type=1,box_index=index,pick_index=1,loc_type=0)
        assert len(first.items)==1
        assert check(sock,"TreasureBoxPickup",33,box_type=1,box_index=index,pick_index=1,loc_type=0)==first
        rest=check(sock,"TreasureBoxPickup",34,box_type=1,box_index=index,pick_index=-1,loc_type=0)
        assert len(rest.items)==len(opened.items)-1
        for item in (*first.items,*rest.items):
            add_item(actual,item.main_item)
            if item.pack_type==pb.ItemDetail.PackType_TempPack:
                add_item(to_store,item.main_item)
        assert actual==expected
        assert not check(sock,"TreasureBoxPickup",35,box_type=1,box_index=index,pick_index=-1,loc_type=0).items
        stored=check(sock,"TempPackItemStore",36)
        actual_store=Counter()
        for item in stored.items:add_item(actual_store,item.main_item)
        assert actual_store==to_store
    with resident(channels,credentials) as sock:
        scene,after=snapshot(sock)
        assert not any(e.battle_id==key and e.box_id==300000 for e in scene.encounter_data)
        assert all(after[item]==before[item]+count for item,count in expected.items()),(expected,after-before)
        assert not check(sock,"TempPackItemStore",10).items
    print(f"PASS TLS: {marked}/{total} previewed encounters marked, scene 1 encounter {key}, "
          f"{len(units)} kill signatures, one chest {index}, {len(opened.items)} entries, "
          f"{sum(expected.values())} units, two relogins, exact inventory delta and no duplicate grant.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--address",required=True)
    parser.add_argument("--ca",required=True,type=Path)
    parser.add_argument("--resources",required=True,type=Path)
    args=parser.parse_args()
    run(args.address,args.ca.resolve(),args.resources.resolve())
