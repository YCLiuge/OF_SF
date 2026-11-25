"""Restore only catalog-linked field progress present in a personal scene snapshot."""
import json

from private_dungeons import _table
from private_field_activities import map_of_riddle
from private_region_devices import catalog
from proto import net_pb2 as pb
from utils import db


def validate(value, scene):
    from save_import_gameplay import number, numbers, rows
    if type(value) is not dict or set(value) - {"scene","devices","riddles","challenges","collectibles","goals"}:
        raise ValueError("invalid field progress")
    if not scene or number(value.get("scene")) != scene:
        raise ValueError("field progress belongs to a different scene")
    all_devices, by_point, _ = catalog()
    out = {"scene":scene, "devices":[], "riddles":[], "challenges":[], "collectibles":[], "goals":[]}
    owned = {}
    for record in rows(value.get("devices", []), 120):
        aid, state, level = numbers(record, 3)
        config = all_devices.get((scene, aid))
        if config and aid not in owned and state in (0,1) and level in config[1] and (scene,aid) != (100,101):
            owned[aid] = level
            out["devices"].append([aid,state,level])
    seen = set()
    for record in rows(value.get("goals", []), 1024):
        aid, goal, count = numbers(record, 3)
        if aid in owned and goal in all_devices[scene,aid][1][owned[aid]] and goal not in seen and count:
            out["goals"].append([aid,goal,min(count,_table("Achieve","achieve")[goal]["count_param"])])
            seen.add(goal)
    known = _table("CollectionItem", "collection_item")
    seen.clear()
    for record in rows(value.get("collectibles", []), 2048):
        kind, item, status = numbers(record, 3)
        if (1 <= kind <= 6 and item not in seen and status in (0,1)
                and known.get(item, {}).get("new_collection_type") == kind and (scene,kind+11,item) in by_point):
            out["collectibles"].append([kind,item,status])
            seen.add(item)
    know_riddles = _table("Riddle","riddle")
    for rid in sorted({number(i) for i in rows(value.get("riddles", []), 512)}):
        if rid in know_riddles and map_of_riddle(know_riddles[rid]) == scene:
            out["riddles"].append(rid)
    known_challenges = _table("Challenge","challenge")
    best = {}
    for record in rows(value.get("challenges", []), 512):
        cid, elapsed = numbers(record, 2)
        if elapsed and known_challenges.get(cid,{}).get("scene_i_d") == scene:
            best[cid] = min(best.get(cid,elapsed),elapsed)
    out["challenges"] = [[k,v] for k,v in sorted(best.items())]
    return out


def restore(pid, data):
    scene = data["scene"]
    for aid, state, level in data["devices"]:
        device = pb.AreaData(area_id=aid,area_state=state,level=level)
        db.set_area(pid,scene,aid,device.SerializeToString())
    for aid, goal, count in data["goals"]:
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,?,?,?)",
                        (pid,f"import_area_goals:{scene}",str(goal),str(count)))
    for kind, item, status in data["collectibles"]:
        data_row = pb.PBCollectionRewardData(item_id=item,status=status)
        db.set_collection(pid,item,kind,data_row.SerializeToString())
        if status == pb.Reward:
            db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'collection',?)",
                            (pid,str(item)))
    for rid in data["riddles"]:
        db.conn.execute("INSERT OR IGNORE INTO realm_field_activities VALUES(?,?,1,?,?,1,0,0,0)",
                        (pid,scene,rid,pb.Finish))
    for cid, elapsed in data["challenges"]:
        config = _table("Challenge","challenge")[cid]
        stars = sum(elapsed <= 1000*config[f"star_timer{i}"] for i in (1,2,3))
        db.set_challenge(pid,scene,cid,pb.ChallengeData(challenge_id=cid,state=pb.Finish,
                         use_time=elapsed,star=stars).SerializeToString())


def snapshot(conn, pid, result):
    for sid, aid, blob in conn.execute("SELECT scene_id,area_id,area_blob FROM areas WHERE player_id=?", (pid,)):
        if (sid,aid) != (100,101):
            result[f"field_area:{sid}",aid] = blob
    for sid, cid, blob in conn.execute("SELECT scene_id,challenge_id,challenge_blob FROM challenges WHERE player_id=?", (pid,)):
        result[f"field_challenge:{sid}",cid] = blob
    for sid, rid, *state in conn.execute("SELECT scene,id,state,wins,chest,started,available FROM realm_field_activities "
                                         "WHERE player=? AND kind=1", (pid,)):
        result[f"field_riddle:{sid}",rid] = json.dumps(state)
    for kind, item, blob in conn.execute("SELECT collection_type,item_id,item_blob FROM collections WHERE player_id=?", (pid,)):
        result[f"field_collect:{kind}",item] = blob
    for ns, key, val in conn.execute("SELECT ns,k,v FROM player_kv WHERE player_id=? AND ns LIKE 'import_area_goals:%'", (pid,)):
        result["field_goal:"+ns.split(":")[1],int(key)] = str(val)
    for (key,) in conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='collection'", (pid,)):
        if key.isascii() and key.isdecimal():
            result["field_receipt:0",int(key)] = "1"


def undo(conn, pid, table, key, value):
    kind, suffix = table.split(":")
    sid = int(suffix)
    if kind == "field_area":
        if value is None:conn.execute("DELETE FROM areas WHERE player_id=? AND scene_id=? AND area_id=?", (pid,sid,key))
        else:db.set_area(pid,sid,key,value)
    elif kind == "field_challenge":
        if value is None:conn.execute("DELETE FROM challenges WHERE player_id=? AND scene_id=? AND challenge_id=?", (pid,sid,key))
        else:db.set_challenge(pid,sid,key,value)
    elif kind == "field_riddle":
        if value is None:conn.execute("DELETE FROM realm_field_activities WHERE player=? AND scene=? AND kind=1 AND id=?", (pid,sid,key))
        else:conn.execute("INSERT OR REPLACE INTO realm_field_activities VALUES(?,?,1,?,?,?,?,?,?)", (pid,sid,key,*json.loads(value)))
    elif kind == "field_collect":
        if value is None:conn.execute("DELETE FROM collections WHERE player_id=? AND collection_type=? AND item_id=?", (pid,sid,key))
        else:db.set_collection(pid,key,sid,value)
    elif kind == "field_goal":
        ns = f"import_area_goals:{sid}"
        if value is None:conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns=? AND k=?", (pid,ns,str(key)))
        else:conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,?,?,?)", (pid,ns,str(key),value))
    elif kind == "field_receipt":
        if value is None:conn.execute("DELETE FROM claimed_rewards WHERE player_id=? AND system='collection' AND key=?", (pid,str(key)))
        else:conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'collection',?)", (pid,str(key)))
    else:
        raise ValueError("unknown field journal entry")
