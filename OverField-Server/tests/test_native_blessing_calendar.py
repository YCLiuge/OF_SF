"""Exercise edition overrides, private resource delivery and stock blessing node redemptions."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest.mock import patch

import realm_admin
import realm_blessings
from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN
from realm_maintenance import Maintenance
from test_game_realm import join, login
from test_native_gacha import call
from test_native_gacha_atomicity import balance, set_balance
from utils import db


START = "2032-12-24 10:00:00"
END = "2032-12-24 20:00:00"
OPEN = {"id":114,"enabled":True,"opens_at":START,"closes_at":END}
CLOSE = {"id":115,"enabled":False,"opens_at":None,"closes_at":None}
TREES = {"trees":[OPEN,CLOSE]}


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    account = identity.create_profile("祈愿排期")
    game = LocalGameService(identity,root,resources,port=0)

    def owner(method, data=None):
        return realm_admin.execute(root,method,data or {},lambda:None)

    catalog = {row["id"]:row for row in owner("blessings.catalog")["trees"]}
    assert len(catalog)==63 and all(row["nodes"] and row["currency_ids"] for row in catalog.values())
    names = {114:"童趣树",115:"红冬树",100107:"红冬树",116:"棒球树",117:"宴席树",118:"雅韵树",
             119:"净心树",120:"春日甜食",121:"樱络树",20002:"兜财树",20004:"兜财树"}
    assert all(catalog[edition]["name"]==name for edition,name in names.items())
    assert owner("blessings.get")=={"trees":[]}
    assert owner("blessings.set",{"trees":[CLOSE,OPEN]})==TREES
    stage = {"theme":23,"activities":[5087],"arcade":0,"opens_at":START,"closes_at":END}
    event = {"events":[{"id":4008,"enabled":False,"opens_at":None,"closes_at":None}]}
    owner("events.set",event)
    owner("plaza.set",stage)
    assert owner("blessings.get")==TREES and Maintenance(identity).plaza()=={**stage,"blessings":TREES["trees"]}
    assert realm_blessings.validate_plan(Maintenance(identity).plaza())==Maintenance(identity).plaza()
    for bad in ([OPEN,OPEN],[dict(OPEN,id=0)],[dict(OPEN,enabled=1)],[dict(OPEN,opens_at="01/01/2032")],
                [dict(OPEN,closes_at="2030-01-01 00:00:00")], [dict(OPEN,unknown=0)]):
        try:
            owner("blessings.set",{"trees":bad})
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid edition override accepted")
        assert owner("blessings.get")==TREES
    owner("plaza.set",{})
    assert Maintenance(identity).plaza()=={"blessings":TREES["trees"]} and owner("events.get")==event
    rows = json.loads((resources/"data/BlessingTree.json").read_bytes())["blessing_tree_info"]["datas"]
    first = {r["i_d"]:next(n for n in r["blessing_tree_info_group_info"] if not n.get("relate_box_i_d")) for r in rows}
    when = [datetime.fromisoformat(START).replace(tzinfo=CN)]
    def exchange(sock, seq, edition):
        return call(sock,"BlessTreeUnlock",seq,def_id=edition,tree_id=first[edition]["box_i_d"])
    def stamp(instant):
        when[0]=datetime.fromisoformat(instant).replace(tzinfo=CN)

    with patch("private_bless_tree.now",side_effect=lambda:when[0]):
        game.start()
        try:
            sock,_,player = login(game.port,identity.issue_ticket(*account))
            with sock:
                join(sock)
                cost=first[114]["item_i_d"]
                with game.lock,db.conn:
                    set_balance(player,cost,first[114]["item_count"])
                stamp("2032-12-24 09:59:59")
                assert exchange(sock,30,114).status==pb.StatusCode_Activity_Close
                stamp("2032-12-24 20:00:01")
                assert exchange(sock,31,114).status==pb.StatusCode_Activity_Close
                assert balance(player,cost)==first[114]["item_count"]
                stamp(END)
                purchase=exchange(sock,32,114)
                assert purchase.status==pb.StatusCode_OK and purchase.rewards and balance(player,cost)==0
                stamp(catalog[115]["opens_at"])
                assert exchange(sock,33,115).status==pb.StatusCode_Activity_Close
        finally:
            game.stop()
        game.start()
        try:
            sock,_,player=login(game.port,identity.issue_ticket(*account))
            with sock:
                join(sock)
                assert Maintenance(identity).plaza()=={"blessings":TREES["trees"]}
                assert list(call(sock,"BlessTree",34,def_id=114).tress.tree_ids)==[first[114]["box_i_d"]]
                stamp(START)
                assert exchange(sock,35,114).status==pb.StatusCode_PLAYER_BLESS_TREE_EXIST
                owner("blessings.set",{"trees":[]})
                assert Maintenance(identity).plaza()=={} and owner("events.get")==event
                stamp(catalog[115]["opens_at"])
                with game.lock,db.conn:
                    set_balance(player,first[115]["item_i_d"],first[115]["item_count"])
                assert exchange(sock,36,115).status==pb.StatusCode_OK
        finally:
            game.stop()
    print("PASS native blessing edition catalog, independent realm and resource schedules, validation, dates, claims, restart and reset.")


def inspect_bundle(blob):
    from plaza_assets import _lz4
    header=blob.index(b"\0",blob.index(b"\0",12)+1)+1
    total, packed, unpacked, flags=struct.unpack_from(">QIII",blob,header)
    assert total==len(blob)
    start=(header+35)&~15
    info=blob[start:start+packed]
    if flags&63:
        info=_lz4(info,unpacked)
    offset=(start+packed+15)&~15
    count,=struct.unpack_from(">I",info,16)
    chunks=[]
    for n in range(count):
        size,length,compression=struct.unpack_from(">IIH",info,20+10*n)
        block=blob[offset:offset+length]
        chunks.append(_lz4(block,size) if compression&63 else block)
        offset+=length
    assert offset==len(blob)
    return b"".join(chunks)


def client_resource(client):
    from game_dispatch import Assets,PATCH_BUNDLES
    from plaza_assets import _fields, OFF_END, OFF_START,KNOWN_BUNDLES
    assets=Assets(client)
    assert not assets.private_copy, "Use a read-only reference installation for this check"
    stock_bundle=PATCH_BUNDLES[assets.patch]
    with assets.open_stock(stock_bundle+".bundle") as src:
        base=inspect_bundle(src.read())
    version=assets.select_plaza({"blessings":TREES["trees"]})
    assert version!=assets.patch
    name=next(f for f in assets.plaza_versions[version] if f.endswith(".bundle"))
    with assets.open(name) as src:
        variant=inspect_bundle(src.read())
    begin,size=KNOWN_BUNDLES[stock_bundle][0]["BlessingTree"]
    assert base[:begin]==variant[:begin] and base[begin+size:]==variant[begin+size:]
    original,changed=base[begin:begin+size],variant[begin:begin+size]
    assert original!=changed
    expected=bytearray(original)
    seen={}
    for group,start,end in _fields(original,0,size):
        if group!=1:
            continue
        for _,row,end_row in _fields(original,start,end):
            cols={field:(a,b) for field,a,b in _fields(original,row,end_row)}
            tree=cols[1][0]
            stock=realm_blessings.catalog()[tree]
            for field,dt in enumerate((stock["opens_at"],stock["closes_at"]),2):
                a,b=cols[field]
                assert original[a:b].decode()==dt
            seen[tree]=cols
    assert len(seen)==63
    for tree,window in ((114,(START,END)),(115,(OFF_START,OFF_END))):
        for field,value in enumerate(window,2):
            a,b=seen[tree][field]
            expected[a:b]=value.encode()
    assert expected==changed and assets.select_plaza({})==assets.patch
    print("PASS stock 1.1.12 client resource delivery, two selected date pairs, all other bytes unchanged and original manifest restored.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",required=True,type=Path)
    cli.add_argument("--client",type=Path,help="Optional complete unmodified national PC 1.1.12 client")
    args=cli.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.resources)
    if args.client:
        client_resource(args.client)
