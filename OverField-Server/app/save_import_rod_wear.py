"""Owner-only repair for rods created at the legacy 100 durability during a sealed import."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from private_dungeons import _table
from proto import net_pb2 as pb


def equipment_digest(templates):
    return sha256(json.dumps(templates,separators=(",",":")).encode("ascii")).hexdigest()


def export_proof(source, resources):
    from save_import_data import project_file
    from save_import_gameplay import validate
    from utils.res_loader import res
    res.load(resources)
    data=project_file(source)
    uid=data.pop("identity",{}).get("uid")
    if not uid:raise ValueError("Backup has no confirmed public UID")
    plan,_=validate(data)
    gear=plan["gear"]
    wear={i:remaining for _,i,remaining in plan.get("fishing_rods",())}
    rods=[[i,*gear[i-1][1:],remaining] for i,remaining in sorted(wear.items())]
    if not rods:raise ValueError("No confirmed rod durability in source backup")
    return dict(format="of-sf-rod-proof/1",uid=uid,count=len(gear),
                digest=equipment_digest([row[1] for row in gear]),rods=rods)


def validate_proof(proof):
    import re
    if (not isinstance(proof,dict) or proof.get("format")!="of-sf-rod-proof/1"
            or type(proof.get("uid")) is not int or not 0<proof["uid"]<=0xffffffff
            or type(proof.get("count")) is not int or not 1<=proof["count"]<=2200
            or not isinstance(proof.get("digest"),str) or not re.fullmatch("[0-9a-f]{64}",proof["digest"])
            or not isinstance(proof.get("rods"),list) or not 1<=len(proof["rods"])<=proof["count"]):
        raise ValueError("Invalid rod recovery proof")
    seen=set()
    for row in proof["rods"]:
        if not isinstance(row,list) or len(row)!=5 or any(type(x) is not int for x in row):
            raise ValueError("Invalid rod proof row")
        i,template,level,star,wear=row
        cfg=_table("Weapon","weapon").get(template,{})
        if (not 1<=i<=proof["count"] or i in seen or cfg.get("new_weapon_type")!=18
                or not 1<=level<=100 or not 0<=star<=5 or not 0<=wear<=cfg["durability"]):
            raise ValueError("Invalid rod specification")
        seen.add(i)


def old_import(conn, player, slot):
    row=conn.execute("SELECT id,counts FROM private_import_receipt WHERE player=? AND slot=? "
                     "AND state='applied' AND sealed=1",(player,slot)).fetchone()
    if not row or "探索员鱼竿" in json.loads(row[1]):
        raise ValueError("Expected a sealed import from before rod restoration")
    return row[0],json.loads(row[1])["装备"]


def matched_block(before,player,proof):
    # The original importer allocated one increasing instance per validated gear row in
    # one transaction. Match the whole ordered catalogue against the original snapshot.
    # This also disambiguates rods with identical templates inside that import.
    inventory={}
    for iid,raw in before.execute("SELECT instance_id,item_detail_blob FROM items_s WHERE player_id=? ORDER BY instance_id",(player,)):
        detail=pb.ItemDetail.FromString(raw)
        inventory[iid]=detail
    index,template,*_=proof["rods"][0]
    starts=[iid-index+1 for iid,d in inventory.items() if d.main_item.item_id==template]
    matches=[]
    for start in starts:
        ordered=[inventory.get(i) for i in range(start,start+proof["count"])]
        if all(ordered) and equipment_digest([d.main_item.item_id for d in ordered])==proof["digest"]:
            matches.append(start)
    if len(matches)!=1:
        raise ValueError("Historical inventory does not uniquely match the complete imported equipment list")
    return {index:(matches[0]+index-1,inventory[matches[0]+index-1]) for index,*_ in proof["rods"]}


def recover(conn,before,proof,*,apply=False):
    validate_proof(proof)
    rows=conn.execute("SELECT r.player,r.slot FROM private_import_receipt r JOIN realm_public_uids u ON u.player_id=r.player WHERE u.uid=?",(proof["uid"],)).fetchall()
    if len(rows)!=1:raise ValueError("Expected one sealed import for this public UID")
    player,slot=rows[0]
    receipt=old_import(conn,player,slot)
    if old_import(before,player,slot)!=receipt or receipt[1]!=proof["count"]:
        raise ValueError("Archived and current equipment import receipts must match")
    items=matched_block(before,player,proof)
    report={"changes":[],"already_applied":0,"skipped":0}
    for index,template,level,star,source_wear in proof["rods"]:
        instance,old=items[index]
        row=conn.execute("SELECT item_detail_blob FROM items_s WHERE player_id=? AND instance_id=?",(player,instance)).fetchone()
        current=pb.ItemDetail.FromString(row[0]) if row else pb.ItemDetail()
        def matches(detail):
            w=detail.main_item.weapon
            return detail.main_item.HasField("weapon") and (w.instance_id,w.weapon_id,w.level,w.star)==(instance,template,level,star)
        if not matches(old) or not matches(current) or old.main_item.weapon.durability!=100:
            report["skipped"]+=1
            continue
        marker=f"{receipt[0]}:{instance}"
        if conn.execute("SELECT 1 FROM player_kv WHERE player_id=? AND ns='import_rod_wear_v1' AND k=?",(player,marker)).fetchone():
            report["already_applied"]+=1
            continue
        weapon=current.main_item.weapon
        if weapon.durability>100 or source_wear<=100:
            report["skipped"]+=1
            continue
        repaired=weapon.durability+source_wear-100
        report["changes"].append([template,weapon.durability,repaired])
        if apply:
            weapon.durability=repaired
            conn.execute("UPDATE items_s SET item_detail_blob=? WHERE player_id=? AND instance_id=?",(current.SerializeToString(),player,instance))
            conn.execute("INSERT INTO player_kv VALUES(?,'import_rod_wear_v1',?,?)",(player,marker,str(source_wear-100)))
    return report


def recover_offline(state,resources,proof_path,archive,*,apply=False):
    from migrate import read_manifest,copy_hash
    from state_lock import exclusive_state
    from utils.res_loader import res
    res.load(resources)
    if proof_path.stat().st_size>256*1024:raise ValueError("Invalid proof size")
    proof=json.loads(proof_path.read_bytes())
    validate_proof(proof)
    with TemporaryDirectory() as tmp, ZipFile(archive) as z:
        _,files=read_manifest(z)
        old=Path(tmp)/"game.sqlite"
        with z.open("data/game.sqlite") as src, old.open("xb") as out:
            if copy_hash(src,out)!=files["game.sqlite"]:raise ValueError("Invalid archived game database digest")
        with (exclusive_state(state) as root,
              closing(sqlite3.connect(old.as_uri()+"?mode=ro&immutable=1",uri=True)) as before,
              closing(sqlite3.connect((root/"game.sqlite").as_uri()+"?mode=rw",uri=True)) as conn):
            conn.execute("BEGIN IMMEDIATE")
            result=recover(conn,before,proof,apply=apply)
            if apply:conn.commit()
            else:conn.rollback()
            return result
