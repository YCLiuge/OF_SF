"""The PC newcomer page uses actual achievement counts and a bit per claimed milestone."""
from datetime import datetime, timedelta
import time

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res
import private_shop as shop
from realm_events import NEWCOMER
from utils import db


def tasks():
    return res.index("ActivityQuest", "activity_quest")[NEWCOMER]["activity_quest_achieve_items"]


def tiers():
    target = res.index("ActivityQuest", "activity_quest")[NEWCOMER]["activity_quest_reward_i_d"]
    return res.index("ActivityQuest", "activity_quest_reward")[target]["activity_quest_reward_item"]


def _age(player, instant, row):
    timestamp = db.get_players_info(player, "create_time")[0]
    created = datetime.fromtimestamp(timestamp, shop.CN)
    if row.get("opening_time"):
        created = max(created, datetime.fromisoformat(row["opening_time"]).replace(tzinfo=shop.CN))
    # The private realm's daily activity clock turns over at 05:00 China Standard Time.
    cutoff = timedelta(hours=5)
    day = ((instant - cutoff).date() - (created - cutoff).date()).days + 1
    return min(7, max(1, day))


def _stored(player):
    return {key: pb.Achieve.FromString(value).count for key, value in db.conn.execute(
        "SELECT achieve_id,achieve_blob FROM achieve WHERE player_id=? AND achieve_id BETWEEN 80100101 AND 80100710",
        (player,))}


def _counts(player):
    stored = _stored(player)
    team = db.get_players_info(player, "team")[0] or ()
    roster = [pb.Character.FromString(data) for data in db.get_characters(player)]
    pulls = db.conn.execute("SELECT COUNT(*) FROM gacha_record WHERE player_id=?", (player,)).fetchone()[0]
    crafts = dict(db.conn.execute("""SELECT CAST(substr(k,1,instr(k,':')-1) AS INTEGER),SUM(CAST(v AS INTEGER))
                                     FROM player_kv WHERE player_id=? AND ns='life_book' GROUP BY 1""", (player,)))
    configs = res.index("Achieve", "achieve")
    for task in tasks():
        key = task["achieve_i_d"]
        rule = configs[key]
        kind = rule["new_achieve_condition"]
        target = rule["count_param"]
        derived = 0
        if kind == 79:
            derived = sum(c.level >= rule["param"][0] for c in roster)
        elif kind == 120:
            derived = sum(c.star >= rule["param"][0] for c in roster)
        elif kind == 135:
            derived = len({char for char in team if char})
        elif kind == 38:
            derived = pulls
        elif kind in (46, 47, 48):
            life_type = {46: pb.LIFE_TYPE_Handicraft, 47: pb.LIFE_TYPE_Making, 48: pb.LIFE_TYPE_Sewing}[kind]
            derived = crafts.get(life_type, 0)
        elif kind == 136:
            blob = db.get_quest(player, rule["param"][0])
            derived = int(bool(blob) and pb.Quest.FromString(blob).status == pb.QuestStatus_Finish)
        current = min(target, max(stored.get(key, 0), derived))
        if current > stored.get(key, 0):
            db.set_achieve(player, key, pb.Achieve(achieve_id=key, count=current).SerializeToString())
        stored[key] = current
    return stored


def record(session, condition, params=()):
    """Record a successful gameplay transition; repeated no-op requests never reach the call sites."""
    if condition in (127,129,156):
        from private_bless_tasks import record as prayer
        if condition==156:prayer(session,74,only=522002)
        else:prayer(session,condition)
    current = _stored(session.player_id)
    changes = pb.AchieveNotice(status=pb.StatusCode_OK)
    configs = res.index("Achieve", "achieve")
    for task in tasks():
        key = task["achieve_i_d"]
        rule = configs[key]
        if rule["new_achieve_condition"] != condition or tuple(rule.get("param",())) != params:
            continue
        before = current.get(key, 0)
        if before >= rule["count_param"]:
            continue
        after = changes.achieves.add(achieve_id=key, count=before + 1)
        db.set_achieve(session.player_id, key, after.SerializeToString())
    if changes.achieves and NEWCOMER in getattr(session, "active_event_ids", ()):
        session.send(MsgId.AchieveNotice, changes, 0)


def _claimed(player, system):
    return {int(key) for (key,) in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                                   (player, system))}


def _mask(player):
    return sum(1 << index for index in _claimed(player, "newcomer_tier"))


def update(session, instant, row, *, force):
    counts = _counts(session.player_id)
    old = getattr(session, "newcomer_counts", {})
    changed = pb.AchieveNotice(status=pb.StatusCode_OK)
    for key, value in counts.items():
        if value and (force or old.get(key) != value):
            changed.achieves.add(achieve_id=key, count=value)
    if changed.achieves:
        session.send(MsgId.AchieveNotice, changed, 0)
    session.newcomer_counts = counts
    session.newcomer_checked = time.monotonic()
    if force:
        reply = pb.ActivityQuestDataNotice(status=pb.StatusCode_OK)
        info = reply.info.add(activity_id=NEWCOMER, open_day=_age(session.player_id, instant, row),
                              progress_reward=_mask(session.player_id),
                              reward_achieve=sorted(_claimed(session.player_id, "newcomer_goal")))
        for col, attr in (("opening_time", "open_time"), ("closing_time", "close_time")):
            if row.get(col):
                stamp = datetime.fromisoformat(row[col]).replace(tzinfo=shop.CN)
                setattr(info, attr, int(stamp.timestamp() * 1000))
        session.send(MsgId.ActivityQuestDataNotice, reply, 0)


def heartbeat(session, instant):
    if NEWCOMER in getattr(session, "active_event_ids", ()) and time.monotonic() - getattr(session, "newcomer_checked", 0) >= 15:
        update(session, instant, res.index("Activity", "activity")[NEWCOMER], force=False)


def claim(session, req, rsp):
    from private_activity_rewards import _reward
    from private_events import current, settings
    if req.activity_id != NEWCOMER:
        return pb.StatusCode_FAIL
    instant = shop.now()
    row = current(res.index("Activity", "activity")[NEWCOMER], instant, settings())
    if row is None:
        return pb.StatusCode_Activity_Close
    if req.is_progress:
        tier = next((r for r in tiers() if r["index"] == req.param), None)
        if not tier:
            return pb.StatusCode_BAD_REQ
        token = shop.balance(session.player_id, tier["condition_item_i_d"])
        if not token or token.main_item.base_item.num < tier["condition_item_num"]:
            return pb.StatusCode_ITEM_NOT_ENOUGH
        amounts = {tier["reward_item_i_d"]: tier["reward_item_num"]}
        system = "newcomer_tier"
    else:
        task = next((r for r in tasks() if r["achieve_i_d"] == req.param), None)
        if not task:
            return pb.StatusCode_BAD_REQ
        if task["days"] > _age(session.player_id, instant, row):
            return pb.StatusCode_FAIL
        if _counts(session.player_id)[req.param] < res.index("Achieve", "achieve")[req.param]["count_param"]:
            return pb.StatusCode_FAIL
        amounts = {r["item_i_d"]: r["item_num"] for r in task["activity_quest_item"]}
        system = "newcomer_goal"
    rsp.is_progress, rsp.param = req.is_progress, req.param
    status = _reward(session, system, req.param, amounts, rsp)
    if status == pb.StatusCode_OK:
        rsp.reward_id = _mask(session.player_id)
        update(session, instant, row, force=True)
    return status
