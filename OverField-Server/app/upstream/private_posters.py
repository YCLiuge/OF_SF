"""Native poster collection claims and consuming owned instances for star upgrades."""
from collections import Counter
import time

from network.msg_id import MsgId
from private_dungeons import _table, grant, progress, report
from private_equipment import load
from proto import net_pb2 as pb
from utils import db


def ledger(pid, system):
    return {int(key) for key, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                                (pid,system)) if str(key).isdecimal()}


def unlocked(pid):
    # got_ids survives use as materials. Also read existing inventory for saves predating that ledger.
    templates = ledger(pid, "got_ids")
    for raw in db.get_item_detail(pid, table="items_s"):
        item = pb.ItemDetail.FromString(raw).main_item
        if item.HasField("poster"):
            templates.add(item.poster.poster_id)
    catalog = _table("Poster", "poster_illustration")
    return {row["illustration_i_d"]:row["item_i_d"] for row in _table("Poster","poster").values()
            if row["item_i_d"] in templates and catalog.get(row.get("illustration_i_d"),{}).get("is_show")}


def listing(session, req, rsp):
    pid = session.player_id
    owned = unlocked(pid)
    claimed = ledger(pid, "poster_illustration")
    for key,row in _table("Poster","poster_illustration").items():
        if row.get("is_show") and (key in owned or key in claimed):
            rsp.poster_illustrations.add(poster_illustration_id=key, status=pb.Reward if key in claimed else pb.NotReward)
    return pb.StatusCode_OK


def record(session, event):
    counts = ((key, min(rule["count_param"], progress(session.player_id,key)+1))
              for key,rule in _table("Achieve","achieve").items()
              if rule.get("new_achieve_condition") == event and not rule.get("param"))
    report(session,counts)


def reward(session, req, rsp):
    pid, key = session.player_id, req.poster_illustration_id
    if not _table("Poster","poster_illustration").get(key,{}).get("is_show"):
        return pb.StatusCode_CFG_NOT_FOUND
    if key in ledger(pid, "poster_illustration"):
        return pb.StatusCode_POSTER_ILLUSTRATION_REWARDED
    template = unlocked(pid).get(key)
    if not template:
        return pb.StatusCode_POSTER_ILLUSTRATION_NOT_ACTIVE
    quality = _table("Item","item")[template]["quality"]
    # Community award mapping: pay the configured quality award on this claim separately from a duplicate draw.
    rule = _table("GlobalConfig","config","key").get(f"Gacha_Poster_Quality{quality}_Reward",{})
    amounts = Counter()
    for pair in rule.get("values", ()):
        item, n = map(int,pair.split(":"))
        amounts[item] += n
    if not amounts:
        return pb.StatusCode_CFG_NOT_FOUND
    db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key,claim_time) VALUES(?,'poster_illustration',?,?)",
                    (pid,str(key),int(time.time())))
    grant(session,amounts,rsp.items)
    rsp.poster_illustration.poster_illustration_id=key
    rsp.poster_illustration.status=pb.Reward
    record(session,161)
    return pb.StatusCode_OK


def star_up(session, req, rsp):
    pid, key = session.player_id, req.poster_instance_id
    target = load(pid,key,"poster")
    if target is None:
        return pb.StatusCode_BAD_REQ
    poster = target.main_item.poster
    config = _table("Poster","poster").get(poster.poster_id, {})
    tiers = _table("Poster","poster_property").get(config.get("property_i_d"),{}).get("poster_property_group_info",())
    stars = {tier["star"] for tier in tiers}
    if not stars or poster.star not in stars:
        return pb.StatusCode_CFG_NOT_FOUND
    if poster.star == max(stars):
        return pb.StatusCode_POSTER_MAX_STAR
    ids = req.cost_poster_instance_id
    if not ids or key in ids or len(ids) != len(set(ids)):
        return pb.StatusCode_BAD_REQ
    characters = [pb.Character.FromString(raw) for raw in db.get_characters(pid)]
    assigned = {slot.poster_id for char in characters for preset in char.equipment_presets for slot in preset.posters}
    total = poster.star
    for iid in ids:
        material = load(pid,iid,"poster")
        if material is None or iid in assigned:
            return pb.StatusCode_BAD_REQ
        part = material.main_item.poster
        if part.poster_id != poster.poster_id or part.wearer_id or part.star not in stars:
            return pb.StatusCode_BAD_REQ
        total += part.star
    # The original confirms any overflow with the player before sending all chosen instances.
    poster.star = rsp.star = min(total,max(stars))
    for iid in ids:
        db.del_item_detail(pid,iid)
    db.set_item_detail(pid,target.SerializeToString(),instance_id=key)
    rsp.poster_instance_id = key
    rsp.cost_poster_instance_id.extend(ids)
    for char in characters:
        if char.character_id == poster.wearer_id or any(slot.poster_id == key for preset in char.equipment_presets for slot in preset.posters):
            rsp.character.add().CopyFrom(char)
    record(session,160)
    session.send(MsgId.PackNotice,pb.PackNotice(status=pb.StatusCode_OK,items=[target]),0)
    from private_equipment_presets import sync_scene_equipment
    sync_scene_equipment(session, instances=(key,))
    return pb.StatusCode_OK
