"""Validate national star-tree pages and restore only explicitly decoded progress."""
from private_dungeons import _table
from proto import net_pb2 as pb
from utils import db


def validate(data):
    from save_import_gameplay import number, numbers, row, rows
    if type(data) is not dict or set(data)!={"pages","achievements","claimed"}:
        raise ValueError("invalid star tree")
    groups=_table("MoonStone","stone_unlock_data")
    nodes={}
    # The native list includes the shared prerequisites from every national group,
    # even when a group's nodes are not attached to one of the eight active pages.
    achievement_ids={key for group in groups.values() for node in group.get("stone_unlock_data_group_info",())
                     for key in node.get("unlock_achieve_i_d",())}
    for sid,area in _table("MoonStone","stone_area_data","scene_i_d").items():
        boxes=groups[area["unlock_i_d"]]["stone_unlock_data_group_info"]
        nodes[sid]={n["box_i_d"] for n in boxes if n.get("item_count")}
    reward_ids={q["achieve_condition_i_d"] for group in _table("MoonStone","stone_reward_quest_data").values()
                for q in group.get("stone_reward_quest_data_group_info",()) if q.get("achieve_condition_i_d")}
    achievement_ids.update(reward_ids)
    saved={}
    for entry in rows(data["pages"],16):
        sid,values,reward=row(entry,3)
        number(sid)
        if type(reward) is not bool:raise ValueError("invalid star tree reward")
        active={number(k) for k in rows(values,256)}
        if sid in nodes:
            current,taken=saved.setdefault(sid,(set(),False))
            current.update(active & nodes[sid])
            saved[sid]=(current,taken or reward)
    conditions=_table("Achieve","achieve")
    progress={}
    for entry in rows(data["achievements"],2048):
        key,total=numbers(entry,2)
        if key in achievement_ids and key in conditions and total:
            count=min(total,conditions[key]["count_param"])
            if count:progress[key]=max(count,progress.get(key,0))
    claimed={number(key) for key in rows(data["claimed"],2048)} & reward_ids
    return {"pages":[[sid,sorted(active),claimed_page] for sid,(active,claimed_page) in sorted(saved.items()) if active or claimed_page],
            "achievements":[[key,val] for key,val in sorted(progress.items())],"claimed":sorted(claimed)}


def restore(pid,tree):
    def mark(system,ident):
        db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",(pid,system,str(ident)))
    for sid,nodes,reward in tree["pages"]:
        if nodes:
            current=set(db.get_ability_tree(pid,sid))
            if not current.issuperset(nodes):
                db.set_ability_tree(pid,sid,sorted(current.union(nodes)))
        if reward:mark("badge_page",sid)
    for key,count in tree["achievements"]:
        raw=db.get_achieve(pid,key)
        ach=pb.Achieve.FromString(raw) if raw else pb.Achieve(achieve_id=key)
        if count>ach.count:
            ach.count=count
            db.set_achieve(pid,key,ach.SerializeToString())
    for key in tree["claimed"]:
        mark("badge_achieve",key)


def snapshot(conn,pid,result):
    for sid,value in conn.execute("SELECT scene_id,tree_blob FROM ability_tree WHERE player_id=?",(pid,)):
        result["badge_nodes",sid]=value
    for system,key in conn.execute("SELECT system,key FROM claimed_rewards WHERE player_id=? AND system IN ('badge_page','badge_achieve')",(pid,)):
        if str(key).isdecimal():result[system,int(key)]="1"


def undo(conn,pid,kind,key,prior):
    if kind=="badge_nodes":
        if prior is None:
            conn.execute("DELETE FROM ability_tree WHERE player_id=? AND scene_id=?",(pid,key))
        else:
            conn.execute("INSERT OR REPLACE INTO ability_tree(player_id,scene_id,tree_blob) VALUES(?,?,?)",(pid,key,prior))
    elif kind in ("badge_page","badge_achieve"):
        if prior is None:
            conn.execute("DELETE FROM claimed_rewards WHERE player_id=? AND system=? AND key=?",(pid,kind,str(key)))
        else:
            conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",(pid,kind,str(key)))
    else:raise ValueError("invalid star tree rollback key")
