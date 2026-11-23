"""Native pass progress and anniversary wishes for the private realm."""
from datetime import datetime, timedelta
from time import monotonic

from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_activity_rewards import _reward
from private_dungeons import _table
import private_events
import private_shop as shop
from utils import db
from utils.res_loader import res


WISH = 260326131


def _read(player, ns, key, default=0):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k=?",
                          (player, ns, str(key))).fetchone()
    return row[0] if row else default


def _write(player, ns, key, value):
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,?,?,?)", (player, ns, str(key), value))


def _claim_keys(player, ns):
    return {k for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system=?",
                                        (player, ns))}


def _open(activity_id, kind, instant):
    rows = _table("Activity", "activity")
    row = rows.get(activity_id)
    if row is None and kind == 3:
        # Some retail dialogs send the template ID rather than the dated activity ID.
        matches = [r for r in rows.values() if r.get("activity_i_d") == activity_id and r.get("new_activity_type") == kind]
        if len(matches) == 1:
            row = matches[0]
        elif matches:
            overrides = private_events.settings()
            active = [r for r in matches if private_events.current(r, instant, overrides)]
            row = active[0] if len(active) == 1 else None
    if not row or row.get("new_activity_type") != kind:
        return None, pb.StatusCode_CFG_NOT_FOUND
    current = private_events.current(row, instant, private_events.settings())
    return current, pb.StatusCode_OK if current else pb.StatusCode_Activity_Close


def _template(row):
    return _table("BattlePass", "battle_pass").get(row.get("activity_i_d", row["i_d"]))


def _open_pass(request, instant):
    row, status = _open(request.activity_battle_pass_id, 3, instant)
    template = _template(row) if row else None
    return row, template, status if not row or template else pb.StatusCode_CFG_NOT_FOUND


def _stamps(info, row):
    info.activity_id = row["i_d"]
    for field, key in (("open_time", "opening_time"), ("close_time", "closing_time")):
        if row.get(key):
            when = datetime.fromisoformat(row[key]).replace(tzinfo=shop.CN)
            setattr(info, field, int(when.timestamp() * 1000))


def _period(instant):
    day = (instant.astimezone(shop.CN) - timedelta(hours=4)).date()
    return day.isoformat(), (day - timedelta(days=day.weekday())).isoformat()


def _goals(template, instant):
    day, week = _period(instant)
    groups = _table("BattlePass", "battle_pass_queset")
    for kind, field, period in (("day", "daily_achieve_reward_lst", day),
                                ("week", "weekly_achieve_reward_lst", week),
                                ("season", "season_achieve_reward_lst", "season")):
        group = groups.get(template.get(kind + "_quest_i_d"), {})
        for goal in group.get("battle_pass_queset_item", ()):
            yield goal, field, period


def _progress(player, row, goal, period, instant):
    aid, key = row["i_d"], goal["achieve_i_d"]
    rule = _table("Achieve", "achieve")[key]
    limit = rule["count_param"]
    condition = rule.get("new_achieve_condition")
    if condition == 82 and limit == 1:  # National daily login condition.
        return 1
    if condition == 38 and period == "season":  # The realm keeps timestamped recruit history.
        start = datetime.fromisoformat(row["opening_time"]).replace(tzinfo=shop.CN).timestamp() if row.get("opening_time") else 0
        count = db.conn.execute("SELECT COUNT(*) FROM gacha_record WHERE player_id=? AND gacha_time BETWEEN ? AND ?",
                                (player, int(start), int(instant.timestamp()))).fetchone()[0]
        return min(limit, count)
    raw = db.get_achieve(player, key)
    count = pb.Achieve.FromString(raw).count if raw else 0
    if condition == 116:  # Count verified finishes even on realms without a generic achievement tracker.
        finishes = sum(pb.DungeonData.FromString(blob).finish_times for (blob,) in db.conn.execute(
            "SELECT dungeon_blob FROM dungeons WHERE player_id=?", (player,)))
        count = max(count, finishes)
    baseline_key = f"{aid}:{key}:{period}"
    baseline = _read(player, "pass_goal_base", baseline_key, None)
    if baseline is None:
        _write(player, "pass_goal_base", baseline_key, count)
        baseline = count
    return min(limit, max(0, count - baseline))


def _level(template, total):
    level, remaining = 0, total
    for tier in template["battle_pass_level_ups"]:
        if remaining < tier["exp"]:
            break
        remaining -= tier["exp"]
        level = tier["level"]
    return level, remaining


def _max_total(template):
    return sum(tier["exp"] for tier in template["battle_pass_level_ups"])


def _pass_info(session, notice, row, template, instant):
    player, aid = session.player_id, row["i_d"]
    info = notice.info.add()
    _stamps(info, row)
    info.total_exp = _read(player, "pass_xp", aid)
    info.level, info.current_exp = _level(template, info.total_exp)
    info.week_exp = _read(player, "pass_week_xp", f"{aid}:{_period(instant)[1]}")
    info.has_buy = bool(_read(player, "private_pass", template["i_d"]))
    keys = _claim_keys(player, "pass_level")
    for tier in template["battle_pass_level_ups"]:
        level = tier["level"]
        if f"{aid}:{level}:free" in keys:
            info.free_lv_reward_lst.append(level)
        # The existing private gift grants the entire premium track on purchase.
        if info.has_buy and len(tier.get("battle_pass_reward", ())) > 1:
            info.payment_lv_reward_lst.append(level)
    claims = _claim_keys(player, "pass_goal")
    for goal, field, period in _goals(template, instant):
        key = goal["achieve_i_d"]
        if f"{aid}:{key}:{period}" in claims:
            getattr(info, field).append(key)
        notice.quest_info_lst.add(achieve_id=key, count=_progress(player, row, goal, period, instant))


def _wish_goal(row):
    regular = _table("ActivityRegular", "activity_regular")[row["activity_i_d"]]
    pool = _table("ActivityRegular", "activity_regular_pool")[regular["pool_i_d"]]
    return pool["activity_regular_pool_item"][0]


def _wish_days(player, row, target, instant):
    aid = row["i_d"]
    day = (instant.astimezone(shop.CN) - timedelta(hours=4)).date().toordinal()
    db.conn.execute("INSERT OR IGNORE INTO private_login_days VALUES(?,?,?,?,?)",
                    (player, aid, day, 1, int(instant.timestamp()*1000)))
    db.conn.execute("UPDATE private_login_days SET last_day=?,days=min(days+1,?) WHERE player=? AND activity=? AND last_day<?",
                    (day, target, player, aid, day))
    return db.conn.execute("SELECT days FROM private_login_days WHERE player=? AND activity=?", (player, aid)).fetchone()[0]


def _wish_info(session, row, instant):
    goal = _wish_goal(row)
    key = goal["achieve_i_d"]
    target = _table("Achieve", "achieve")[key]["count_param"]
    days = _wish_days(session.player_id, row, target, instant)
    progress = pb.AchieveNotice(status=pb.StatusCode_OK)
    progress.achieves.add(achieve_id=key, count=days)
    session.send(MsgId.AchieveNotice, progress, 0)
    msg = pb.ActivityRegularDataNotice(status=pb.StatusCode_OK)
    entry = msg.info.add()
    _stamps(entry, row)
    entry.param1 = bool(_read(session.player_id, "wish_txt", WISH))
    entry.pool_achieve.add(achieve_id=key, is_reward=str(WISH) in _claim_keys(session.player_id, "wish_days"))
    session.send(MsgId.ActivityRegularDataNotice, msg, 0)


def send(session, instant, rows):
    notice = pb.ActivityBattlePassInfoNotice(status=pb.StatusCode_OK)
    active = []
    for row in rows:
        if row["i_d"] == WISH:
            _wish_info(session, row, instant)
        elif row.get("new_activity_type") == 3:
            template = _template(row)
            if template:
                _pass_info(session, notice, row, template, instant)
                active.append(row)
    session.pass_calendar_rows = active
    session.pass_progress_check = 0
    session.pass_progress_token = _progress_token(session.player_id, active, instant) if active else None
    if notice.info:
        session.send(MsgId.ActivityBattlePassInfoNotice, notice, 0)


def _progress_token(player, rows, instant):
    goals = sorted({goal["achieve_i_d"] for row in rows for goal, _, _ in _goals(_template(row), instant)})
    markers = ",".join("?" for _ in goals)
    saved = tuple(db.conn.execute(f"SELECT achieve_id,achieve_blob FROM achieve WHERE player_id=? AND achieve_id IN ({markers}) ORDER BY achieve_id",
                                  (player, *goals)))
    dungeons = tuple(db.conn.execute("SELECT dungeon_id,dungeon_blob FROM dungeons WHERE player_id=? ORDER BY dungeon_id", (player,)))
    last_draw = db.conn.execute("SELECT max(id) FROM gacha_record WHERE player_id=?", (player,)).fetchone()[0]
    premium = tuple(_read(player, "private_pass", _template(row)["i_d"]) for row in rows)
    return saved, dungeons, last_draw, premium


def refresh_progress(session, instant):
    rows = getattr(session, "pass_calendar_rows", ())
    current = monotonic()
    if not rows or current < getattr(session, "pass_progress_check", 0):
        return
    session.pass_progress_check = current + 15
    token = _progress_token(session.player_id, rows, instant)
    if token == session.pass_progress_token:
        return
    session.pass_progress_token = token
    notice = pb.ActivityBattlePassInfoNotice(status=pb.StatusCode_OK)
    for row in rows:
        _pass_info(session, notice, row, _template(row), instant)
    session.send(MsgId.ActivityBattlePassInfoNotice, notice, 0)


def level_reward(session, req, rsp):
    row, template, status = _open_pass(req, shop.now())
    if status != pb.StatusCode_OK:
        return status
    player, aid = session.player_id, row["i_d"]
    tier = next((t for t in template["battle_pass_level_ups"] if t["level"] == req.level), None)
    if not tier:
        return pb.StatusCode_CFG_NOT_FOUND
    if not req.is_get_free_reward:
        return (pb.StatusCode_BattlePass_LvRewarded if _read(player, "private_pass", template["i_d"])
                else pb.StatusCode_BattlePass_Not_Buy)
    level, _ = _level(template, _read(player, "pass_xp", aid))
    if level < req.level:
        return pb.StatusCode_BattlePass_LevelLimit
    reward = tier["battle_pass_reward"][0]
    status = _reward(session, "pass_level", f"{aid}:{req.level}:free",
                     {reward["item_i_d"]: reward["item_num"]}, rsp)
    return pb.StatusCode_BattlePass_LvRewarded if status == pb.StatusCode_REPEAT_GET_REWARD else status


def quest_reward(session, req, rsp):
    instant = shop.now()
    row, template, status = _open_pass(req, instant)
    if status != pb.StatusCode_OK:
        return status
    player, aid = session.player_id, row["i_d"]
    match = next(((goal, period) for goal, _, period in _goals(template, instant)
                  if goal["achieve_i_d"] == req.achieve_id), None)
    if not match:
        return pb.StatusCode_CFG_NOT_FOUND
    goal, period = match
    key = f"{aid}:{req.achieve_id}:{period}"
    if key in _claim_keys(player, "pass_goal"):
        return pb.StatusCode_BattlePass_QuestRewarded
    if _progress(player, row, goal, period, instant) < _table("Achieve", "achieve")[req.achieve_id]["count_param"]:
        return pb.StatusCode_FAIL
    total = _read(player, "pass_xp", aid)
    week_key = f"{aid}:{_period(instant)[1]}"
    week = _read(player, "pass_week_xp", week_key)
    exp = min(goal["exp"], max(0, template["max_exp"] - week), _max_total(template) - total)
    if exp <= 0:
        return pb.StatusCode_BattlePass_MaxLevel if total >= _max_total(template) else pb.StatusCode_FAIL
    if not db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,?,?)",
                           (player, "pass_goal", key)).rowcount:
        return pb.StatusCode_BattlePass_QuestRewarded
    _write(player, "pass_xp", aid, total+exp)
    _write(player, "pass_week_xp", week_key, week+exp)
    rsp.add_exp, rsp.current_total_exp, rsp.current_week_exp = exp, total+exp, week+exp
    rsp.current_lv, rsp.current_exp = _level(template, total+exp)
    return pb.StatusCode_OK


def buy_exp(session, req, rsp):
    row, template, status = _open_pass(req, shop.now())
    if status != pb.StatusCode_OK:
        return status
    player, aid = session.player_id, row["i_d"]
    total = _read(player, "pass_xp", aid)
    maximum = _max_total(template)
    if total >= maximum:
        return pb.StatusCode_BattlePass_MaxLevel
    config = next(r for r in res["GlobalConfig"]["config"]["datas"] if r.get("key") == "BattlePass_BuyExp_Currency")
    item, cost, exp = map(int, config["values"][0].split(":"))
    purse = shop.balance(player, item)
    if not purse or purse.main_item.WhichOneof("item") != "base_item" or purse.main_item.base_item.num < cost:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    purse.main_item.base_item.num -= cost
    db.set_item_detail(player, purse.SerializeToString(), item)
    total = min(total + exp, maximum)
    _write(player, "pass_xp", aid, total)
    rsp.activity_battle_pass_id, rsp.total_exp = req.activity_battle_pass_id, total
    rsp.battle_pass_lv, rsp.current_exp = _level(template, total)
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    pack.items.add().CopyFrom(purse)
    session.send(MsgId.PackNotice, pack, 0)
    return pb.StatusCode_OK


def wish_text(session, req, rsp):
    instant = shop.now()
    if req.activity_id != WISH:
        return pb.StatusCode_CFG_NOT_FOUND
    row, status = _open(WISH, 4, instant)
    if not row:
        return status
    if not req.txt.strip() or len(req.txt.encode("utf8")) > 1024 or any(ord(c) < 32 for c in req.txt):
        return pb.StatusCode_BAD_REQ
    _write(session.player_id, "wish_txt", WISH, req.txt)
    rsp.activity_id, rsp.txt = WISH, req.txt
    _wish_info(session, row, instant)
    return pb.StatusCode_OK


def wish_claim(session, req, rsp):
    instant = shop.now()
    if req.activity_id != WISH:
        return pb.StatusCode_CFG_NOT_FOUND
    row, status = _open(WISH, 4, instant)
    if not row:
        return status
    goal = _wish_goal(row)
    if req.pool_type != 3 or req.param != goal["achieve_i_d"]:
        return pb.StatusCode_BAD_REQ
    target = _table("Achieve", "achieve")[req.param]["count_param"]
    if _wish_days(session.player_id, row, target, instant) < target:
        return pb.StatusCode_FAIL
    rsp.param, rsp.pool_type = req.param, req.pool_type
    amounts = {r["item_i_d"]: r["item_num"] for r in goal["activity_regular_pool_reward_item"]}
    return _reward(session, "wish_days", WISH, amounts, rsp)
