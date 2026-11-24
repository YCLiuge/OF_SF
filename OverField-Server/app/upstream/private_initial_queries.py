"""Read native HUD initialization data from the national PC tables and the private player's save."""
import random
import time
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_PlayerBriefInfo
from utils.res_loader import res


def table(resource, key):
    return res[resource][key]["datas"]


def value(pid, ns, key, default=None):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k=?",(pid,ns,key)).fetchone()
    return row[0] if row else default


def friends(session, req, rsp):
    if req.type not in (0,1,2,3):
        return pb.StatusCode_BAD_REQ
    rows=db.get_friend_info(session.player_id,None,"friend_id,friend_status,alias,friend_tag,friend_intimacy,friend_background")
    for friend,state,alias,tag,intimacy,bg in rows:
        if state and (req.type == 0 or req.type == state):
            entry=rsp.info.add(alias=alias, friend_tag=tag, friend_intimacy=intimacy, friend_background=bg)
            make_PlayerBriefInfo(friend,entry.info)
    return pb.StatusCode_OK


def badges(session, req, rsp):
    pid=session.player_id
    scenes={r["scene_i_d"] for r in table("MoonStone","stone_area_data")}
    all_pages=db.get_ability_tree(pid)
    pages=(req.scene_id,) if req.scene_id else sorted(all_pages)
    for scene in pages:
        if scene in scenes:
            rsp.ability_badge_pages.add(page=scene, active_box_ids=all_pages.get(scene,()))
    unlocks={aid for row in table("MoonStone","stone_unlock_data") for node in row.get("stone_unlock_data_group_info",())
             for aid in node.get("unlock_achieve_i_d",())}
    rewards={rule["achieve_condition_i_d"] for group in table("MoonStone","stone_reward_quest_data")
             for rule in group.get("stone_reward_quest_data_group_info",()) if rule.get("achieve_condition_i_d")}
    for aid in sorted(unlocks|rewards):
        raw=db.get_achieve(pid,aid)
        if raw:
            achieve=pb.Achieve.FromString(raw)
            if achieve.count or aid in unlocks:
                rsp.ability_badge_achieves.add().CopyFrom(achieve)
    ids=db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='badge_achieve' ORDER BY key",(pid,))
    rsp.ability_badge_reward_achieve_ids.extend(int(key) for (key,) in ids if str(key).isdigit())
    return pb.StatusCode_OK


def scene_progress(session,req,rsp):
    from private_map_progress import points
    for row in table("Scene","scene_progress"):
        scene=row["i_d"]
        count=points(session.player_id,scene)
        if count>0:
            rsp.scene_process_list.add(scene_id=scene,process=count)
    return pb.StatusCode_OK


def supply(session,req,rsp):
    pid=session.player_id
    when=value(pid,"player","supply_box_next")
    if when is None:
        existing=db.conn.execute("SELECT next_reward_time FROM supply_box WHERE player_id=?",(pid,)).fetchone()
        if existing:
            when=existing[0]
        else:
            timer=next(r for r in table("GlobalConfig","config") if r["key"]=="SupplyBox_Random_Time")
            low,high=map(int,timer["values"])
            created=db.get_players_info(pid,"create_time")[0]
            when=created+random.randint(low,high)*60
            db.set_SupplyBox_next_reward_time(pid,when)
    rsp.next_reward_time=int(when)
    return pb.StatusCode_OK


def generic(session,req,rsp):
    # The upstream PC region query (6) returns one integer parameter, with the response ID unset.
    if req.generic_msg_id != 6:
        rsp.generic_msg_id=req.generic_msg_id
        return pb.StatusCode_FAIL
    rsp.params.add().int_value=1003
    return pb.StatusCode_OK
