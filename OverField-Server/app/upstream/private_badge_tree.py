"""National MoonStone node requirements, private inventories and per-player rewards."""
from collections import Counter

from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from private_badge_config import ABILITY_BY_ITEM, NODE_REWARDS
from private_dungeons import _table
from private_initial_queries import badges
import private_shop as shop
from utils import db


def _area(page):
    return _table("MoonStone", "stone_area_data", "scene_i_d").get(page)


def _boxes(area):
    group = _table("MoonStone", "stone_unlock_data")[area["unlock_i_d"]]
    return {node["box_i_d"]: node for node in group["stone_unlock_data_group_info"] if node.get("item_count")}


def _owned(player, item):
    detail = shop.balance(player, item)
    return detail is not None and detail.main_item.WhichOneof("item") == "base_item" and detail.main_item.base_item.num > 0


def _access(player, area):
    item = area["unlock_item_i_d"]
    unlock = _table("PlayerUnlock", "player_unlock", "item_i_d").get(item)
    known = db.get_players_info(player, "unlock_functions")[0] or ()
    return (unlock is not None and unlock["i_d"] in known) or _owned(player, item)


def _achieve_count(player, key):
    rule = _table("Achieve", "achieve").get(key, {})
    raw = db.get_achieve(player, key)
    old = pb.Achieve.FromString(raw).count if raw else 0
    value = old
    if rule.get("new_achieve_condition") == 36:  # Captain level.
        value = max(value, db.get_players_info(player, "level")[0])
    elif rule.get("new_achieve_condition") == 44 and rule.get("param"):  # The learned double-jump item.
        value = max(value, int(_owned(player, rule["param"][0])))
    if value > old:
        db.set_achieve(player, key, pb.Achieve(achieve_id=key, count=value).SerializeToString())
    return value


def _completed(player, key):
    target = _table("Achieve", "achieve").get(key, {}).get("count_param", 0)
    return target > 0 and _achieve_count(player, key) >= target


def _claimed(player, category):
    return {int(k) for (k,) in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                               (player,category)) if k.isdigit()}


def _mark(player, category, key):
    return db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",
                           (player, category, str(key))).rowcount != 0


def _active(player, page):
    data = db.get_ability_tree(player, page)
    return set(data) if isinstance(data, (list,tuple,set)) else set()


def starter_talk(session, req, packet_id):
    from utils.pb_create import make_QuestNotice
    player = session.player_id
    raw = db.get_quest(player, 100031)
    quest = pb.Quest.FromString(raw) if raw else None
    goal = next((c for c in quest.conditions if c.condition_id == req.id), None) if quest else None
    from private_story_objectives import at_fount
    if req.talk_type != pb.NpcTalkReq.NpcTalkType_Npc or not goal or not at_fount(session):
        session.send(MsgId.NpcTalkRsp, pb.NpcTalkRsp(status=pb.StatusCode_BAD_REQ), packet_id)
        return
    if goal.status == pb.QuestStatus_InProgress:
        notice = make_QuestNotice(session, [req.id])
        if notice and notice.quests:
            session.send(MsgId.QuestNotice, notice, 0)
    elif goal.status not in (pb.QuestStatus_Finish, pb.QuestStatus_Completed):
        session.send(MsgId.NpcTalkRsp, pb.NpcTalkRsp(status=pb.StatusCode_BAD_REQ), packet_id)
        return
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    if _mark(player, "badge_starter", 100031) and not _owned(player,100000008) and not _active(player,1):
        shop.award(player, {100000008:1, 301:1}, pb.ShopBuyRsp(), pack)
    session.send(MsgId.NpcTalkRsp, pb.NpcTalkRsp(status=pb.StatusCode_OK), packet_id)
    if pack.items:
        session.send(MsgId.PackNotice, pack, 0)
    from private_story_objectives import learned
    learned(session)


def list_badges(session, req, rsp):
    for group in _table("MoonStone", "stone_unlock_data").values():
        for node in group.get("stone_unlock_data_group_info", ()):
            for key in node.get("unlock_achieve_i_d", ()):
                _achieve_count(session.player_id, key)
    status = badges(session, req, rsp)
    taken = _claimed(session.player_id, "badge_page")
    for page in rsp.ability_badge_pages:
        page.is_reward = page.page in taken
    return status


def list_abilities(session, req, rsp):
    player = session.player_id
    marks = ",".join("?" for _ in ABILITY_BY_ITEM)
    inventory = db.conn.execute(f"SELECT item_id,item_detail_blob FROM items WHERE player_id=? AND item_id IN ({marks})",
                                (player,*ABILITY_BY_ITEM))
    for key, blob in inventory:
        info = pb.ItemDetail.FromString(blob).main_item
        if info.WhichOneof("item") == "base_item" and info.base_item.num:
            db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'ability',?,0)", (player,str(ABILITY_BY_ITEM[key])))
    for key, val in db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='ability' ORDER BY CAST(k AS INTEGER)",
                                    (player,)):
        if str(key).isdigit() and isinstance(val, (int,str)) and str(val).isdigit():
            rsp.ability_list.add(ability_id=int(key), level=int(val))
    return pb.StatusCode_OK


def _grant(player, rewards, rsp, pack, notices):
    delivery = pb.ShopBuyRsp()
    shop.award(player, rewards, delivery, pack)
    rsp.items.extend(delivery.items)
    current = list(db.get_players_info(player, "unlock_functions")[0] or ())
    added = []
    for item in rewards:
        unlock = _table("PlayerUnlock", "player_unlock", "item_i_d").get(item)
        if unlock is not None and unlock["i_d"] not in current:
            added.append(unlock["i_d"])
            current.append(unlock["i_d"])
        ability = ABILITY_BY_ITEM.get(item)
        if ability and db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'ability',?,0)",
                                       (player,str(ability))).rowcount:
            notices.append((MsgId.PlayerAbilityUnlockNotice,
                            pb.PlayerAbilityUnlockNotice(status=pb.StatusCode_OK,ability_id=ability)))
    if added:
        db.set_players_info(player, "unlock_functions", current)
        notices.append((MsgId.PlayerUnlockFunctionNotice,
                        pb.PlayerUnlockFunctionNotice(status=pb.StatusCode_OK, unlock_functions=added)))


def activate(session, req, rsp, pack, notices):
    player = session.player_id
    area = _area(req.page)
    rsp.page, rsp.box_id = req.page, req.box_id
    if not area:
        return pb.StatusCode_CFG_NOT_FOUND
    nodes = _boxes(area)
    node = nodes.get(req.box_id)
    awards = NODE_REWARDS.get(req.page, {}).get(req.box_id)
    if not node or not awards:
        return pb.StatusCode_CFG_NOT_FOUND
    active = _active(player, req.page)
    if req.box_id in active:  # Official response on a replay contains no second grant.
        return pb.StatusCode_OK
    if not _access(player, area):
        return pb.StatusCode_ABILITY_BADGE_NOT_UNLOCK
    neighbors = node.get("relate_box_i_ds", ())
    if (neighbors and not active.intersection(neighbors)) or not all(
            _completed(player, condition) for condition in node.get("unlock_achieve_i_d", ())):
        return pb.StatusCode_ABILITY_BADGE_ACTIVE_PRE_CONDITION_NOT_FINISH
    item, cost = node["item_i_d"], node["item_count"]
    stock = shop.balance(player, item)
    if not stock or stock.main_item.WhichOneof("item") != "base_item" or stock.main_item.base_item.num < cost:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    stock.main_item.base_item.num -= cost
    db.set_item_detail(player, stock.SerializeToString(), item)
    pack.items.add().CopyFrom(stock)
    _grant(player, dict(awards), rsp, pack, notices)
    db.set_ability_tree(player, req.page, sorted(active | {req.box_id}))
    if req.page == 1 and req.box_id == 31:
        from private_story_objectives import learned
        learned(session)
        msg = pb.AchieveNotice(status=pb.StatusCode_OK)
        for key in {k for n in nodes.values() for k in n.get("unlock_achieve_i_d",())}:
            if _table("Achieve","achieve")[key].get("new_achieve_condition") == 44:
                msg.achieves.add(achieve_id=key, count=_achieve_count(player,key))
        if msg.achieves:
            notices.append((MsgId.AchieveNotice,msg))
    return pb.StatusCode_OK


def page_reward(session, req, rsp, pack, notices):
    player = session.player_id
    area = _area(req.page)
    rsp.page = req.page
    if not area:
        return pb.StatusCode_CFG_NOT_FOUND
    if req.page in _claimed(player, "badge_page"):
        return pb.StatusCode_ABILITY_BADGE_PAGE_REWARDED
    if not _access(player, area):
        return pb.StatusCode_ABILITY_BADGE_NOT_UNLOCK
    if len(_active(player, req.page).intersection(_boxes(area))) < area["unlock_count"]:
        return pb.StatusCode_ABILITY_BADGE_PAGE_BOX_ACTIVE_LIMIT
    # National string_moon_stone_text explicitly gives 10 limited tickets on each of these eight pages.
    if area.get("reward_text_i_d") not in {1001,1002,1003,1004,1005,1051,1052,1053}:
        return pb.StatusCode_CFG_NOT_FOUND
    if not _mark(player, "badge_page", req.page):
        return pb.StatusCode_ABILITY_BADGE_PAGE_REWARDED
    _grant(player, {110:10}, rsp, pack, notices)
    return pb.StatusCode_OK


def achievement_reward(session, req, rsp, pack, notices):
    player = session.player_id
    if not req.achieve_ids:
        return pb.StatusCode_BAD_REQ
    catalog = {}
    groups = _table("MoonStone","stone_reward_quest_data")
    for area in _table("MoonStone","stone_area_data").values():
        for rule in groups[area["reward_quest_i_d"]]["stone_reward_quest_data_group_info"]:
            catalog[rule["achieve_condition_i_d"]] = area
    keys = dict.fromkeys(req.achieve_ids)
    if any(key not in catalog for key in keys):
        return pb.StatusCode_CFG_NOT_FOUND
    previous = _claimed(player, "badge_achieve")
    amounts = Counter()
    for key in keys:
        if key in previous or not _completed(player, key) or not _access(player, catalog[key]):
            continue
        if _mark(player, "badge_achieve", key):
            rsp.achieve_ids.append(key)
            amounts[catalog[key]["item_i_d"]] += 1
    if not amounts:
        return (pb.StatusCode_ABILITY_BADGE_ACHIEVE_REWARDED if set(keys) <= previous
                else pb.StatusCode_ABILITY_BADGE_ACHIEVE_NOT_COMPLETE)
    _grant(player, amounts, rsp, pack, notices)
    return pb.StatusCode_OK


def handler(name, operation):
    request = getattr(pb, name + "Req")
    response = getattr(pb, name + "Rsp")

    @packet_handler(getattr(MsgId, name + "Req"))
    class Handler(PacketHandler):
        def handle(self, session, data, packet_id):
            req, rsp = request.FromString(data), response()
            notices = []
            pack = pb.PackNotice(status=pb.StatusCode_OK)
            if not db.conn.in_transaction:
                db.conn.execute("BEGIN")
            db.conn.execute("SAVEPOINT private_badge")
            try:
                rsp.status = operation(session, req, rsp, pack, notices)
            except (shop.UnsupportedGoods, ValueError, OverflowError):
                db.conn.execute("ROLLBACK TO private_badge")
                rsp.Clear()
                rsp.status = pb.StatusCode_CFG_NOT_FOUND
            except BaseException:
                db.conn.execute("ROLLBACK TO private_badge")
                raise
            finally:
                db.conn.execute("RELEASE private_badge")
            session.send(getattr(MsgId, name + "Rsp"), rsp, packet_id)
            if rsp.status == pb.StatusCode_OK:
                if pack.items:
                    session.send(MsgId.PackNotice, pack, 0)
                for msg_id, notice in notices:
                    session.send(msg_id, notice, 0)
    return Handler
