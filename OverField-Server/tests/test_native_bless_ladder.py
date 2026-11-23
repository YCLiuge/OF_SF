"""Drive every scheduled blessing ladder through native frames; test funding, node types and boundaries."""
import argparse
from contextlib import ExitStack
from datetime import datetime,timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from private_shop import CN, UnsupportedGoods, balance
from test_game_realm import join,login
from test_observed_initialization import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root/"identity.sqlite3")
    accounts = [identity.create_profile(name) for name in ("兑换甲","兑换乙")]
    host = LocalGameService(identity,root,resources,port=0)
    table = json.loads((resources/"data/BlessingTree.json").read_bytes())
    layouts = {r["i_d"]:r["blessing_tree_info_group_info"] for r in table["blessing_tree_info"]["datas"]}
    calendar = {r["blessing_tree_info_i_d"]:r for r in table["blessing_tree"]["datas"]}
    currencies = {node["item_i_d"] for rows in layouts.values() for node in rows}
    assert len(calendar) == 63 and len(layouts) == 77 and len(currencies) == 13
    assert sum(map(len,layouts.values())) == 1034
    assert sum(len(layouts[n]) for n in calendar) == 911
    for nodes in layouts.values():
        assert len({n["box_i_d"] for n in nodes}) == len(nodes)
        found=set()
        for node in nodes:
            assert not node.get("relate_box_i_d") or node["relate_box_i_d"] in found
            found.add(node["box_i_d"])

    def online(stack,index):
        sock,_,pid = login(host.port,identity.issue_ticket(*accounts[index]))
        stack.enter_context(sock)
        join(sock)
        return sock,pid

    def exchange(sock,tree,node):
        return call(sock,"BlessTreeUnlock",def_id=tree,tree_id=node)

    def progress(sock,tree):
        rsp=call(sock,"BlessTree",def_id=tree)
        assert rsp.status == pb.StatusCode_OK and rsp.HasField("tress")
        return set(rsp.tress.tree_ids)

    def units(pid,item):
        with host.lock:
            purse=balance(pid,item)
            return purse.main_item.base_item.num if purse else 0

    def instant(stamp):
        return datetime.fromisoformat(stamp).replace(tzinfo=CN)

    def asof(when):
        return patch("private_bless_tree.now",return_value=when)

    host.start()
    try:
        with ExitStack() as live,asof(instant("2026-11-24 10:00:00")):
            player,pid=online(live,0)
            peer,other=online(live,1)
            assert not progress(player,10012)
            assert exchange(player,0,0).status==pb.StatusCode_CFG_NOT_FOUND
            assert exchange(player,10012,990).status==pb.StatusCode_CFG_NOT_FOUND
            assert 990 not in progress(player,10012)
            assert exchange(player,10012,32).status==pb.StatusCode_PLAYER_BLESS_NEED_NODE
            with host.lock,db.conn:
                db.conn.execute("DELETE FROM items WHERE player_id IN (?,?) AND item_id=125",(pid,other))
            assert not units(pid,125)
            assert exchange(player,10012,11).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert not progress(player,10012)
            # These are national shop 400's fixed bundles. The native shop credits accumulated currency.
            deposit=call(player,"ShopBuy",shop_id=400,grid_id=27,buy_times=3)
            assert deposit.status==pb.StatusCode_OK and units(pid,125) >= 37
            before=units(pid,125)
            for node in (11,21,32,20):
                rsp=exchange(player,10012,node)
                assert rsp.status==pb.StatusCode_OK and rsp.def_id==10012 and rsp.tree_id==node and rsp.rewards
            assert units(pid,125)==before-27 and progress(player,10012)=={11,20,21,32}
            assert exchange(player,10012,32).status==pb.StatusCode_PLAYER_BLESS_TREE_EXIST
            assert units(pid,125)==before-27 and not progress(peer,10012) and units(other,125)==0
            assert not progress(player,101)
            assert exchange(player,101,11).status==pb.StatusCode_Activity_Close
            assert exchange(player,100108,11).status==pb.StatusCode_Activity_Close
            with host.lock,db.conn:
                db.set_bless_tree(other,10012,[0,999,999])
                db.set_bless_tree(other,4294967295,[999])
            assert not progress(peer,10012)
            # Inject a grant failure after the fee is debited, and check both database writes rolled back.
            with patch("private_bless_tree.shop.award",side_effect=UnsupportedGoods):
                assert exchange(player,10012,31).status == pb.StatusCode_CFG_NOT_FOUND
            assert units(pid,125)==before-27 and progress(player,10012)=={11,20,21,32}
        host.stop()
        host.start()
        with ExitStack() as live:
            player,same=online(live,0)
            peer,other=online(live,1)
            assert same==pid and units(pid,125)==before-27 and progress(player,10012)=={11,20,21,32}
            assert not progress(peer,10012)
            # The full matrix uses wealthy, isolated test inventories for all 13 real currency IDs.
            with host.lock,db.conn:
                for item in currencies:
                    db.set_item_detail(other,make_item(item,20000,other).SerializeToString(),item)
            for tree,period in calendar.items():
                start,end = instant(period["opening_time"]),instant(period["closing_time"])
                root_node=layouts[tree][0]["box_i_d"]
                with asof(start-timedelta(seconds=1)):
                    assert exchange(peer,tree,root_node).status==pb.StatusCode_Activity_Close
                with asof(end+timedelta(seconds=1)):
                    assert exchange(peer,tree,root_node).status==pb.StatusCode_Activity_Close
                for idx,node in enumerate(layouts[tree]):
                    with asof(start if idx==0 else end):
                        rsp=exchange(peer,tree,node["box_i_d"])
                    assert rsp.status==pb.StatusCode_OK, (tree,node,rsp.status)
                    assert len(rsp.rewards)==1 and rsp.rewards[0].main_item.item_id==node["reward_item_i_d"]
                    info=rsp.rewards[0].main_item
                    kind=info.WhichOneof("item")
                    if kind=="base_item":
                        assert info.base_item.num==node["reward_item_count"]
                    elif kind in ("weapon","poster"):
                        with host.lock:
                            assert db.get_item_detail(other,instance_id=getattr(info,kind).instance_id)
                    else:
                        assert kind=="outfit" and info.outfit.outfit_id==node["reward_item_i_d"]
                assert progress(peer,tree)=={n["box_i_d"] for n in layouts[tree]}
            assert progress(player,10012)=={11,20,21,32}
            old_rewards=[(101,20,4100022,pb.EBagItemTag_Fashion), (10001,22,1001003,pb.EBagItemTag_Weapon),
                         (30001,30,11100,pb.EBagItemTag_Poster)]
            with host.lock,db.conn:
                for tree,key,template,tag in old_rewards:
                    db.set_bless_tree(pid,tree,[11,key])
                    broken=pb.ItemDetail()
                    broken.main_item.item_id=template
                    broken.main_item.item_tag=tag
                    broken.main_item.base_item.num=1
                    db.set_item_detail(pid,broken.SerializeToString(),template)
                def instances(template):
                    return sum(pb.ItemDetail.FromString(raw).main_item.item_id==template
                               for (raw,) in db.conn.execute("SELECT item_detail_blob FROM items_s WHERE player_id=?",(pid,)))
                totals={template:instances(template) for _,_,template,_ in old_rewards}
        for _ in range(2):
            host.stop()
            host.start()
            with ExitStack() as live:
                player,_=online(live,0)
                with host.lock:
                    assert balance(pid,4100022).main_item.WhichOneof("item")=="outfit"
                    for template in (1001003,11100):
                        assert balance(pid,template) is None and instances(template)==totals[template]+1
    finally:
        host.stop()
    print("PASS 77 layouts, 63 scheduled ladders/911 native redemptions, 13 currencies, native shop funding, branching, typed inventory, duplicates, role isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as scratch:
        run(Path(scratch),parser.parse_args().resources)
