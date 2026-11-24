"""Use national collection template IDs and receipts for archaeology claims."""
from proto import net_pb2 as pb
from utils import db
from private_activity_rewards import deliver_once
from private_collection_queries import positive_item
from private_dungeons import _table, reward_items, report

CATALOG = {pb.EArchaeologyType_Book:"Book", pb.EArchaeologyType_TreasureMap:"Treasure",
           pb.EArchaeologyType_Utensil:"Utensil", pb.EArchaeologyType_Furniture:"Furniture",
           pb.EArchaeologyType_Shovel:"Shovel"}


def entries(category):
    name = CATALOG.get(category)
    return _table(name,name.lower()) if name else {}


def collected(pid,row):
    template = row.get("item_i_d",row["i_d"])
    if db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='got_ids' AND key=?",
                       (pid,str(template))).fetchone():
        return True
    raw = db.get_item_detail(pid,template)
    return bool(raw and positive_item(raw))


def collected_count(pid,category):
    templates = [row.get("item_i_d",row["i_d"]) for row in entries(category).values() if row.get("collection_i_d")]
    if not templates:
        return 0
    marks = ",".join("?" for _ in templates)
    history = {int(key) for key, in db.conn.execute(
        f"SELECT key FROM claimed_rewards WHERE player_id=? AND system='got_ids' AND key IN ({marks})",
        (pid,*map(str,templates)))}
    # Pre-ledger saves may have an item without a historical receipt.
    if len(history) < len(set(templates)):
        for key,raw in db.conn.execute(f"SELECT item_id,item_detail_blob FROM items WHERE player_id=? AND item_id IN ({marks})",
                                       (pid,*templates)):
            if key not in history and positive_item(raw):
                history.add(key)
    return sum(key in history for key in templates)


def conditions(category):
    return next((r["items"] for r in _table("ArchaeologyAchieve","archaeology_achieve").values()
                 if r.get("new_archaeology_type",0)==category),())


def illustration(session,req,rsp):
    pid = session.player_id
    table = entries(req.archaeology_type)
    row = table.get(req.id) or next((r for r in table.values() if r.get("item_i_d")==req.id),None)
    if not row or not row.get("collection_i_d"):
        return pb.StatusCode_CFG_NOT_FOUND
    if not collected(pid,row):
        return pb.StatusCode_ITEM_NOT_ENOUGH
    rsp.archaeology_type,rsp.id = req.archaeology_type,req.id
    key = f"{req.archaeology_type}:{row['i_d']}"
    # Accept legacy receipt keys that used the inventory template rather than the exhibit number.
    old = f"{req.archaeology_type}:{row.get('item_i_d',row['i_d'])}"
    if old!=key and db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='treasure_illustration' AND key=?",
                                   (pid,old)).fetchone():
        return pb.StatusCode_REPEAT_GET_REWARD
    status = deliver_once(session,"treasure_illustration",key,reward_items(pid,row["collection_i_d"]),rsp.rewards)
    if status==pb.StatusCode_OK:
        count = collected_count(pid,req.archaeology_type)
        report(session,((r["achieve_i_d"],min(count,_table("Achieve","achieve")[r["achieve_i_d"]]["count_param"]))
                        for r in conditions(req.archaeology_type)))
    return status


def achievement(session,req,rsp):
    entry = next((r for r in conditions(req.archaeology_type) if r["achieve_i_d"]==req.achieve_id),None)
    if not entry:
        return pb.StatusCode_CFG_NOT_FOUND
    pid = session.player_id
    previous = db.get_achieve(pid,req.achieve_id)
    count = max(pb.Achieve.FromString(previous).count if previous else 0,collected_count(pid,req.archaeology_type))
    if count < _table("Achieve","achieve")[req.achieve_id]["count_param"]:
        return pb.StatusCode_FAIL
    status = deliver_once(session,"treasure_achieve",req.achieve_id,reward_items(pid,entry["reward_i_d"]),rsp.items)
    if status == pb.StatusCode_OK:
        rsp.rewarded_achieve_ids.extend(sorted(int(k) for k, in db.conn.execute(
            "SELECT key FROM claimed_rewards WHERE player_id=? AND system='treasure_achieve'",(pid,)) if str(k).isdigit()))
    return status
