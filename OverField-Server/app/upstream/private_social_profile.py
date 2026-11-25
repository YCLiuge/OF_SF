"""Player profile visibility, private friend labels and intimacy gifts for this realm."""
from datetime import timedelta
import unicodedata

from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_crafts import payments, save_pack
from utils.res_loader import res
from private_friends import peer_exists, state
from private_shop import now
from utils import db
from utils.pb_create import make_PlayerBriefInfo, update_birthday


def config(name):
    return int(res.index("GlobalConfig", "config", "key")[name]["values"][0])


def visibility(session, req, rsp):
    flag = req.hide_type
    if flag not in (pb.HideType_Account_Type, pb.HideType_SIGN_Type, pb.HideType_Abyss_Rank_Type):
        return pb.StatusCode_BAD_REQ
    old, = db.get_players_info(session.player_id, "hide_value")
    rsp.hide_value = int(old or 0) ^ flag
    db.set_players_info(session.player_id, "hide_value", rsp.hide_value)
    return pb.StatusCode_OK


def birthday(session, req, rsp):
    old, = db.get_players_info(session.player_id, "is_hide_birthday")
    db.set_players_info(session.player_id, "is_hide_birthday", int(not old))
    update_birthday(session)
    return pb.StatusCode_OK


def sex(session, req, rsp):
    if req.sex not in pb.ESexType.values():
        return pb.StatusCode_BAD_REQ
    rsp.sex = req.sex
    db.set_players_info(session.player_id, "sex", req.sex)
    return pb.StatusCode_OK


def profile(session, req, rsp):
    other, player = req.player_id, session.player_id
    if not other or not peer_exists(other):
        return pb.StatusCode_FRIEND_SEARCH_FAIL
    make_PlayerBriefInfo(other, rsp.other_info)
    fields = db.get_friend_info(player, other, "friend_status,alias,friend_tag,friend_intimacy,friend_background")
    if fields[0] is not None:
        rsp.friend_status, rsp.alias, rsp.friend_tag, rsp.friend_intimacy, rsp.friend_background = fields
    if state(other, player) == pb.FriendStatus_BLACK:
        rsp.friend_status = pb.FriendStatus_BLACK
    return pb.StatusCode_OK


def friend_points(player, other):
    if (not other or other == player or state(player, other) != pb.FriendStatus_FRIEND or
            state(other, player) != pb.FriendStatus_FRIEND):
        return None
    return int(db.get_friend_info(player, other, "friend_intimacy")[0] or 0)


def stage(points):
    return max((row["intimacy_level"] for row in res.index("Intimacy", "intimacy_stage").values()
                if points >= row.get("intimacy_condition_value", 0)), default=0)


def labels(session, req, rsp):
    other = req.player_id
    points = friend_points(session.player_id, other)
    if points is None:
        return pb.StatusCode_FRIEND_NOT_EXIST
    if not req.data or len(req.data) > 3:
        return pb.StatusCode_BAD_REQ
    changes = {}
    level = stage(points)
    for field in req.data:
        if field.type == 1:
            key, value = "alias", field.value
            if field.int_value or len(value) > 64 or any(unicodedata.category(c) in ("Cc", "Cs") for c in value):
                return pb.StatusCode_BAD_REQ
        elif field.type in (2, 3):
            key = "friend_tag" if field.type == 2 else "friend_background"
            value = field.int_value
            if field.value:
                return pb.StatusCode_BAD_REQ
            if value:
                rows = res.index("Intimacy", "intimacy_label").values()
                attr = "label_text_i_d" if field.type == 2 else "icon_i_d"
                matches = [r for r in rows if r.get(attr) and value in (r["i_d"], r[attr])]
                if not matches:
                    return pb.StatusCode_CFG_NOT_FOUND
                if level < max(config("IntimacyLevelUnLockTag"), min(r["intimacy_level"] for r in matches)):
                    return pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
        else:
            return pb.StatusCode_BAD_REQ
        if key in changes:
            return pb.StatusCode_BAD_REQ
        changes[key] = value
    for key, value in changes.items():
        db.set_friend_info(session.player_id, other, key, value)
    rsp.player_id = other
    rsp.data.extend(req.data)
    return pb.StatusCode_OK


def daily(player):
    day = (now() - timedelta(hours=config("SystemRefreshTime"))).date().isoformat()
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='friend_gifts' AND k='daily'",
                          (player,)).fetchone()
    saved, _, count = str(row[0]).partition("/") if row else ("", "", "")
    return day, int(count) if day == saved else 0


def gift_count(session, count):
    session.send(MsgId.IntimacyGiftDayCountNotice, pb.IntimacyGiftDayCountNotice(status=pb.StatusCode_OK, count=count), 0)


def gift(session, req, rsp):
    player, other = session.player_id, req.friend_id
    rsp.friend_id = other
    current = friend_points(player, other)
    if current is None:
        return pb.StatusCode_FRIEND_NOT_EXIST
    rsp.intimacy = current
    cfg = res.index("Item", "item").get(req.item_id, {})
    gain = cfg.get("relate_i_d", 0)
    if cfg.get("new_bag_item_tag") != pb.EBagItemTag_IntimacyGift or gain <= 0:
        return pb.StatusCode_BAD_REQ
    limit = config("IntimacyMax")
    if current >= limit:
        return pb.StatusCode_FRIEND_INTIMACY_LIMIT
    if stage(current) < config("IntimacyLevelUnLockGift"):
        return pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
    day, used = daily(player)
    if used >= config("IntimacyGiftMaxUseNumber"):
        return pb.StatusCode_FRIEND_INTIMACY_DAY_GIFT_LIMIT
    costs, status = payments(player, {req.item_id: 1})
    if status != pb.StatusCode_OK:
        return status
    rsp.intimacy = min(current + gain, limit)
    for owner, peer in ((player, other), (other, player)):
        db.set_friend_info(owner, peer, "friend_intimacy", rsp.intimacy)
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'friend_gifts','daily',?)",
                    (player, f"{day}/{used + 1}"))
    save_pack(session, costs)
    gift_count(session, used + 1)
    return pb.StatusCode_OK
