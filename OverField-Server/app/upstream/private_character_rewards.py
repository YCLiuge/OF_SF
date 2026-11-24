"""National character medal milestones and private-realm reward tracks."""
from proto import net_pb2 as pb
from utils import db
from private_achievement_lists import character_list
from private_dungeons import _table
from private_shop import balance
from private_activity_rewards import deliver_once


def receive(session, req, rsp, badge):
    info = pb.GetCharacterAchievementListRsp()
    status = character_list(session, req, info)
    if status != pb.StatusCode_OK:
        return status
    cid = req.character_id
    char = pb.Character.FromString(db.get_characters(session.player_id, cid)[0])
    config = _table("Character", "character")[cid]
    if badge:
        key = req.achievement_id
        claimed = set(info.rewarded_id_lst)
        output = rsp.rewarded_achievement_id_lst
        entries = _table("Character", "character_achieve")[config["character_achieve_i_d"]]["achieve_info"]
        row = next((r for r in entries if r["achieve_i_d"] == key), None)
    else:
        rsp.character_id = cid
        key = req.reward_index
        claimed = set(info.has_rewarded_ids)
        output = rsp.rewarded_index_lst
        entries = _table("Character", "character_achieve_reward")[config["achieve_reward_i_d"]]["achieve_reward_info"]
        row = next((r for r in entries if r["num"] == key), None)
    if row is None:
        return pb.StatusCode_CFG_NOT_FOUND
    if key in claimed:
        output.extend(sorted(claimed))
        return pb.StatusCode_REPEAT_GET_REWARD
    if badge:
        goal = _table("Achieve", "achieve")[key]["count_param"]
        progress = next(a for a in info.character_achievement_lst if a.achieve_id == key)
        if progress.count < goal:
            return pb.StatusCode_Quest_Not_Finish
        template, quantity = row["item_i_d"], row["item_count"]
        system, stored, items = "chr_badge", char.rewarded_achievement_id_lst, rsp.update_items
    else:
        if row.get("new_reward_price_type") and not char.is_unlock_payment:
            return pb.StatusCode_MODULE_CLOSE
        wallet = balance(session.player_id, row["unlock_item_i_d"])
        if not wallet or wallet.main_item.base_item.num < row["unlock_item_count"]:
            return pb.StatusCode_Quest_Not_Finish
        template, quantity = row.get("reward_item_i_d"), row.get("reward_item_count", 0)
        system, stored, items = "chr_achieve", char.reward_index_lst, rsp.items
    status = deliver_once(session, system, f"{cid}:{key}", {template: quantity}, items)
    if status != pb.StatusCode_OK:
        return status
    stored.append(key)
    db.set_character(session.player_id, cid, char.SerializeToString())
    output.extend(sorted(claimed | {key}))
    if badge:
        rsp.achieves.add().CopyFrom(progress)
    return pb.StatusCode_OK


def badge_reward(session, req, rsp):
    return receive(session, req, rsp, True)


def tier_reward(session, req, rsp):
    return receive(session, req, rsp, False)


def unlock_track(session, req, rsp):
    cid = req.character_id
    raws = db.get_characters(session.player_id, cid) if cid else ()
    if not raws or cid not in _table("Character", "character"):
        return pb.StatusCode_CharNotExist
    char = pb.Character.FromString(raws[0])
    if not char.is_unlock_payment:
        char.is_unlock_payment = True
        db.set_character(session.player_id, cid, char.SerializeToString())
    rsp.character_id = cid
    rsp.is_unlock = True
    return pb.StatusCode_OK
