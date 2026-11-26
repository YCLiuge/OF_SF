"""Stopped-realm repair must preserve subsequent wear and never recreate discarded rods."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from migrate import backup
from proto import net_pb2 as pb
from save_import_rod_wear import equipment_digest,recover_offline
from state_lock import exclusive_state,StateBusy


def check(root,resources):
    realm=root/"realm"
    realm.mkdir()
    game=realm/"game.sqlite"
    with closing(sqlite3.connect(realm/"identity.sqlite3")) as c:
        c.execute("CREATE TABLE test_identity(slot TEXT)")
    slots=[1006001,1006005,1006006,1006010]
    def item(instance,template,wear):
        detail=pb.ItemDetail()
        main=detail.main_item
        main.item_id=main.weapon.weapon_id=template
        main.weapon.instance_id=instance
        main.weapon.level=main.weapon.star=1
        main.weapon.durability=wear
        return detail.SerializeToString()
    with closing(sqlite3.connect(game)) as c,c:
        c.executescript("""
            CREATE TABLE realm_public_uids(player_id INTEGER,uid INTEGER);
            CREATE TABLE private_import_receipt(slot TEXT PRIMARY KEY,player INTEGER,id TEXT,counts TEXT,state TEXT,sealed INTEGER);
            CREATE TABLE items_s(player_id INTEGER,instance_id INTEGER,item_detail_blob BLOB,PRIMARY KEY(player_id,instance_id));
            CREATE TABLE player_kv(player_id INTEGER,ns TEXT,k TEXT,v BLOB,PRIMARY KEY(player_id,ns,k));
            INSERT INTO realm_public_uids VALUES(9,123456);
            INSERT INTO private_import_receipt VALUES('s',9,'receipt1','{"装备":4}','applied',1);
        """)
        for index,template in enumerate(slots,11):
            c.execute("INSERT INTO items_s VALUES(9,?,?)",(index,item(index,template,100)))
    previous=root/"before.zip"
    backup(realm,previous)
    proof=root/"proof.json"
    evidence=dict(format="of-sf-rod-proof/1",uid=123456,count=4,digest=equipment_digest(slots),
                  rods=[[i+1,t,1,1,1000 if i==0 else 1500] for i,t in enumerate(slots)])
    proof.write_text(json.dumps(evidence),"utf8")
    with closing(sqlite3.connect(game)) as c,c:
        c.execute("UPDATE items_s SET item_detail_blob=? WHERE player_id=9 AND instance_id=14",(item(14,slots[3],0),))
        c.execute("DELETE FROM items_s WHERE player_id=9 AND instance_id=12")
        c.execute("INSERT INTO items_s VALUES(9,15,?)",(item(15,slots[1],600),))
        c.execute("INSERT INTO items_s VALUES(10,11,?)",(item(11,slots[0],50),))
    def run(apply=False):
        return recover_offline(realm,resources,proof,previous,apply=apply)
    with exclusive_state(realm):
        try:run(True)
        except StateBusy:pass
        else:raise AssertionError("Live realm accepted a migration")
    expected=[[1006001,100,1000],[1006006,100,1500],[1006010,0,1400]]
    preview=run()
    assert preview=={"changes":expected,"skipped":1,"already_applied":0} and run()==preview
    proof.write_text(json.dumps(dict(evidence,digest="d"*64)),"utf8")
    try:run(True)
    except ValueError:pass
    else:raise AssertionError("A different source import was accepted")
    proof.write_text(json.dumps(evidence),"utf8")
    with closing(sqlite3.connect(game)) as c,c:
        c.execute("CREATE TRIGGER stop_repair BEFORE UPDATE ON items_s WHEN OLD.instance_id=14 BEGIN SELECT RAISE(ABORT,'fixture'); END")
    try:run(True)
    except sqlite3.IntegrityError:pass
    else:raise AssertionError("Expected a failed partial migration")
    assert run()==preview
    with closing(sqlite3.connect(game)) as c,c:
        c.execute("DROP TRIGGER stop_repair")
    assert run(True)==preview
    assert run(True)=={"changes":[],"skipped":1,"already_applied":3}
    with closing(sqlite3.connect(game)) as c:
        stored={(p,i):pb.ItemDetail.FromString(raw).main_item.weapon.durability
                for p,i,raw in c.execute("SELECT * FROM items_s")}
        assert stored=={(9,11):1000,(9,13):1500,(9,14):1400,(9,15):600,(10,11):50}
    print("PASS archived receipt + full equipment fingerprint, preview, offline lock, atomic rollback, wear delta, idempotence, disposed/reacquired and foreign instances.")


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as folder:
        check(Path(folder),cli.parse_args().resources)
