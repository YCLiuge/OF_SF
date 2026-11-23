"""Exercise actual 1.1.12 temporary bag and warehouse messages over TCP in a disposable realm."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
from migrate import backup, restore
from private_inventory import slots
from test_game_dungeon import call
from test_game_realm import login, join
from utils import db
from utils.pb_create import make_item


def run(base, resources):
    root = base / "realm"
    root.mkdir()
    accounts = LocalIdentity(root / "identity.sqlite3")
    a,b = [accounts.create_profile(name) for name in ("背包甲","背包乙")]
    host = LocalGameService(accounts,root,resources,port=0)
    scene = 100
    def connect(profile):
        sock,_,_ = login(host.port,accounts.issue_ticket(*profile))
        join(sock)
        return sock
    def move(sock, new_scene):
        assert call(sock,"ChangeSceneChannel",scene_id=new_scene,channel_label=1).status == pb.StatusCode_OK
    def storage(pid, template):
        raw = db.get_item_detail(pid,template)
        return pb.ItemDetail.FromString(raw).main_item.base_item.num if raw else 0
    def box(pid, index, grants):
        with host.lock, db.conn:
            data=pb.TreasureBoxData(index=index,box_id=3001,state=pb.TreasureBoxState_Open)
            for template, count in grants:
                detail=make_item(template,count,pid)
                detail.pack_type=pb.ItemDetail.PackType_TempStorageArea
                data.rewards.add().CopyFrom(detail)
            db.conn.execute("INSERT INTO private_boxes(player_id,zone,dynamic,idx,template,data) VALUES(?,?,1,?,3001,?)",
                            (pid,scene,index,data.SerializeToString()))
        return index
    def pick(sock, index):
        return call(sock,"TreasureBoxPickup",box_type=1,box_index=index,pick_index=-1)
    def amounts(entries):
        return [i.base_item.num for i in entries]

    host.start()
    try:
        with connect(a) as first, connect(b) as second:
            move(first,scene)
            move(second,scene)
            pid=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(a[0],)).fetchone()[0]
            other=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(b[0],)).fetchone()[0]
            original=storage(pid,1000)
            coins=storage(pid,101)
            initial_weapons=call(first,"GetWeapon").total_num
            # A native chest with guaranteed configured stackable materials and coins.
            owned=box(pid,41,[(1000,8),(101,2),(1000,7)])
            assert pick(second,owned).status!=pb.StatusCode_OK
            acquired=pick(first,owned)
            assert acquired.status==pb.StatusCode_OK and [i.main_item.item_id for i in acquired.items]==[1000,101,1000,1000]
            assert [i.main_item.base_item.num for i in acquired.items]==[8,2,2,5]
            assert all(i.pack_type==pb.ItemDetail.PackType_TempPack for i in acquired.items if i.main_item.item_id==1000)
            assert not pick(first,owned).items
            assert storage(pid,1000)==original and storage(pid,101)==coins+2
            assert not call(second,"TempPackSort").temp_pack.item_infos
            sorted_pack=call(first,"TempPackSort").temp_pack
            assert sorted_pack.max_size==30 and amounts(sorted_pack.item_infos)==[10,5]
            assert call(second,"TempPackItemDrop",index=2).status==pb.StatusCode_BAD_REQ
            assert call(first,"TempPackItemDrop",index=300).status==pb.StatusCode_BAD_REQ
            assert amounts(call(first,"TempPackItemDrop",index=2).temp_pack.item_infos)==[10]
            # A full pickup, including an otherwise acceptable coin grant, rejects atomically.
            over=box(pid,42,[(101,99),(1000,300)])
            assert pick(first,over).status==pb.StatusCode_Temp_Pack_Is_Full
            assert storage(pid,101)==coins+2 and amounts(call(first,"TempPackSort").temp_pack.item_infos)==[10]
            assert db.conn.execute("SELECT claimed FROM private_boxes WHERE player_id=? AND idx=42",(pid,)).fetchone()==(0,)
            # Two accounts can own slots with the same index, independently.
            assert pick(second,box(other,41,[(1000,2)])).status==pb.StatusCode_OK
            assert len(call(second,"TempPackItemStore").items)==1
            assert storage(other,1000)>=2 and storage(pid,1000)==original
            assert not call(second,"TempPackItemStore").items
            # A compatible weapon can be equipped without knowing another account's state.
            weapon=pick(first,box(pid,43,[(1101104,1)])).items[0]
            instance=weapon.main_item.weapon.instance_id
            index=weapon.main_item.temp_pack_index
            assert call(first,"TempPackWearEquip",index=index,char_id=999).status==pb.StatusCode_BAD_REQ
            assert call(first,"TempPackWearEquip",index=index,char_id=101001).status==pb.StatusCode_OK
            equipped=call(first,"GetWeapon")
            assert equipped.total_num==initial_weapons+1
            assert any(w.instance_id==instance and w.wearer_id==101001 for w in equipped.weapons)
            paged=call(first,"GetWeapon",start_index=1,end_index=2)
            assert paged.total_num==equipped.total_num and list(paged.weapons)==list(equipped.weapons)[1:2]
            assert call(first,"GetWeapon",start_index=6,end_index=1).status==pb.StatusCode_BAD_REQ
            assert all(w.instance_id!=instance or w.wearer_id!=101001 for w in call(second,"GetWeapon").weapons if w.instance_id>initial_weapons)
    finally:
        host.stop()

    archive=backup(root,base/"inventory.zip")
    restored=restore(archive,base/"restored")
    accounts=LocalIdentity(restored/"identity.sqlite3")
    host=LocalGameService(accounts,restored,resources,port=0)
    host.start()
    try:
        with connect(a) as first:
            assert amounts(call(first,"TempPackSort").temp_pack.item_infos)==[10]
            assert storage(pid,1000)==original
            move(first,9999)
            assert not slots(pid) and storage(pid,1000)==original+10
            assert not call(first,"TempPackItemStore").items and storage(pid,1000)==original+10
    finally:
        host.stop()
    print("PASS configured exploration inventory, atomic overflow, owner-only item movement and equipment, warehouse pagination, migration and return-home deposit.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)

