"""Publish supported native activity pages and award the PC daily login dessert."""
from datetime import datetime, timedelta
from functools import lru_cache
import json

from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import _table
import private_shop as shop
from realm_events import DAILY, NEWCOMER, WISH, SCHEMA
from utils import db


def setup():
    db.conn.execute("CREATE TABLE IF NOT EXISTS authority.private_event_config " + SCHEMA)


@lru_cache(maxsize=4)
def _overrides(raw):
    return {row["id"]: row for row in json.loads(raw)["events"]} if raw else {}


def settings():
    row = db.conn.execute("SELECT settings FROM authority.private_event_config WHERE id=1").fetchone()
    return _overrides(row[0] if row else None)


def current(activity, instant, overrides):
    override = overrides.get(activity["i_d"])
    if override:
        if not override["enabled"]:
            return None
        activity = dict(activity)
        for field, column in (("opens_at", "opening_time"), ("closes_at", "closing_time")):
            if override[field] is not None:
                activity[column] = override[field]
    return activity if shop.in_window(activity, instant) else None


def _goals():
    daily = _table("ActivityRegular", "activity_regular")[DAILY]
    return _table("ActivityRegular", "activity_regular_pool")[daily["daily_pool_i_d"]]["activity_regular_pool_item"]


def _day(instant):
    # 国服「每日甜点」页面标为每日 05:00 刷新。
    return (instant - timedelta(hours=5)).date().isoformat()


def _claim_key(day, goal):
    return f"{DAILY}:{day}:{goal}"


def _notice_daily(session, day, activity):
    counts = pb.AchieveNotice(status=pb.StatusCode_OK)
    notice = pb.ActivityRegularDataNotice(status=pb.StatusCode_OK)
    entry = notice.info.add(activity_id=DAILY)
    for field, column in (("open_time", "opening_time"), ("close_time", "closing_time")):
        if activity.get(column):
            stamp = datetime.fromisoformat(activity[column]).replace(tzinfo=shop.CN)
            setattr(entry, field, int(stamp.timestamp() * 1000))
    for goal in _goals():
        aid = goal["achieve_i_d"]
        expected = _table("Achieve", "achieve")[aid]
        if expected.get("new_achieve_condition") != 82 or expected.get("count_param") != 1:
            raise ValueError("Unsupported daily dessert condition")
        claimed = db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system=? AND key=?",
                                  (session.player_id, "daily_dessert", _claim_key(day, aid))).fetchone()
        entry.daily_achieve.add(achieve_id=aid, is_reward=bool(claimed))
        counts.achieves.add(achieve_id=aid, count=1)
    session.send(MsgId.AchieveNotice, counts, 0)
    session.send(MsgId.ActivityRegularDataNotice, notice, 0)


def refresh(session, force=False):
    from private_battle_pass import refresh_progress, send
    from private_panel_quests import activity_dialogue
    import private_newcomer
    instant = shop.now()
    overrides = settings()
    if (not force and getattr(session, "event_overrides", None) is overrides
            and getattr(session, "event_checked", instant) <= instant < getattr(session, "next_event_check", instant)):
        refresh_progress(session, instant)
        private_newcomer.heartbeat(session, instant)
        return
    rows = {}
    # Sign-in counts renew at 04:00, whereas the native dessert page specifies 05:00.
    midnight = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    bounds = [midnight + timedelta(hours=hour) for hour in (4, 5, 28)]
    for activity in _table("Activity", "activity").values():
        aid = activity["i_d"]
        if aid in (DAILY, NEWCOMER, WISH) or activity.get("new_activity_type") in (1, 3) or activity_dialogue(activity):
            override = overrides.get(aid, {})
            if not override.get("enabled", True):
                continue
            for column, field, seconds in (("opening_time", "opens_at", 0), ("closing_time", "closes_at", 1)):
                value = override.get(field) or activity.get(column)
                if value:
                    bounds.append(datetime.fromisoformat(value).replace(tzinfo=shop.CN) + timedelta(seconds=seconds))
            row = current(activity, instant, overrides)
            if row:
                rows[aid] = row
    # A persistent client receives changes on its next normal heartbeat, including the daily reset.
    day = _day(instant)
    sign_day = (instant - timedelta(hours=4)).date()
    fingerprint = (day, sign_day, tuple((aid, row.get("opening_time"), row.get("closing_time")) for aid, row in rows.items()),
                   tuple(sorted(overrides)))
    session.event_overrides = overrides
    session.event_checked = instant
    session.next_event_check = min(bound for bound in bounds if bound > instant)
    if not force and getattr(session, "event_calendar", None) == fingerprint:
        return
    before = getattr(session, "active_event_ids", set())
    closed = (before | set(overrides)) - rows.keys()
    for is_open, ids in ((False, closed), (True, rows.keys())):
        if ids:
            session.send(MsgId.ActivityChangeNotice, pb.ActivityChangeNotice(
                status=pb.StatusCode_OK, is_open=is_open, activity_id=sorted(ids)), 0)
    from private_activity_rewards import send_signs
    send_signs(session, instant, rows.values())
    if DAILY in rows:
        _notice_daily(session, day, rows[DAILY])
    send(session, instant, rows.values())
    if NEWCOMER in rows:
        private_newcomer.update(session, instant, rows[NEWCOMER], force=True)
    session.active_event_ids = set(rows)
    session.event_calendar = fingerprint


def claim(session, req, rsp):
    from private_activity_rewards import _reward
    if req.activity_id == WISH:
        from private_battle_pass import wish_claim
        return wish_claim(session, req, rsp)
    if req.activity_id != DAILY:
        return pb.StatusCode_FAIL
    instant = shop.now()
    activity = current(_table("Activity", "activity")[DAILY], instant, settings())
    if activity is None:
        return pb.StatusCode_Activity_Close
    goal = next((row for row in _goals() if row["achieve_i_d"] == req.param), None)
    # UIActivitySystem.GetPoolType returns 1 for a goal in the daily pool.
    if req.pool_type != 1 or goal is None:
        return pb.StatusCode_BAD_REQ
    amounts = {r["item_i_d"]: r["item_num"] for r in goal["activity_regular_pool_reward_item"]}
    day = _day(instant)
    rsp.param, rsp.pool_type = req.param, req.pool_type
    status = _reward(session, "daily_dessert", _claim_key(day, req.param), amounts, rsp)
    if status == pb.StatusCode_OK:
        _notice_daily(session, day, activity)
    return status


def use_potion(session, req, rsp):
    cfg = _table("Item", "item")[req.item_id]
    currency = 10 if cfg["new_bag_item_tag"] == pb.EBagItemTag_Vitality else 11
    stock = shop.balance(session.player_id, req.item_id)
    if req.choose_item_id:
        return pb.StatusCode_BAD_REQ
    if not stock or stock.main_item.base_item.num < req.num:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    amount = cfg["relate_i_d"] * req.num
    old = shop.balance(session.player_id, currency)
    if amount + (old.main_item.base_item.num if old else 0) > 0xffffffff:
        return pb.StatusCode_BAD_REQ
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    stock.main_item.base_item.num -= req.num
    db.set_item_detail(session.player_id, stock.SerializeToString(), req.item_id)
    notice.items.add().CopyFrom(stock)
    shop.award(session.player_id, {currency: amount}, rsp, notice)
    session.send(MsgId.PackNotice, notice, 0)
    from private_newcomer import record
    record(session, 146 if currency == 10 else 147)
    return pb.StatusCode_OK
