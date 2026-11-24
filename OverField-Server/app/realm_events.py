"""Owner overrides for native activity pages supported by this realm."""
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path

from local_identity import LocalIdentity
from state_lock import exclusive_state


DAILY = 4008
WISH = 260326131
NEWCOMER = 1001
SCHEMA = "(id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)"
DATA = Path(__file__).resolve().parents[1] / "resources" / "data"
SIGN_TITLES = {
    2001: "新人签到", 2002: "14日签到", 2003: "预约奖励", 2004: "「拾光少女」签到",
    2006: "快乐六一", 2007: "祈愿季七日", 2008: "祈愿季三日", 2010: "庆典登录礼",
    2011: "欢聚庆典14日", 2012: "集结未来14日", 2013: "最初的邂逅", 2014: "校园14日",
    2015: "雪铃启新", 2016: "姜饼工坊", 2017: "万象更新", 2018: "圣诞签到",
    5053: "14日签到", 100502: "14日签到", 260212100: "启岁同歌", 260212101: "月满春序",
    260212102: "三元纳福", 260212103: "巧日得春", 260212104: "万家灯火",
    260326100: "周年庆签到（10日签到）", 260430100: "51签到（8日签到）",
    260625100: "七日签到", 260715100: "七日签到", 260815110: "七日签到",
}


@lru_cache(maxsize=1)
def _catalog():
    def table(resource, group):
        data = json.loads((DATA / (resource + ".json")).read_bytes())
        return {row["i_d"]: row for row in data[group]["datas"] if "i_d" in row}

    signs = table("ActivitySign", "activity_sign")
    regular = table("ActivityRegular", "activity_regular")[DAILY]
    daily_pool = table("ActivityRegular", "activity_regular_pool")[regular["daily_pool_i_d"]]
    passes = table("BattlePass", "battle_pass")
    event_pools = table("ActivityRegular", "activity_regular_pool")
    wish_pool = table("ActivityRegular", "activity_regular")[WISH]["pool_i_d"]
    newcomer = table("ActivityQuest", "activity_quest")[NEWCOMER]
    tiers = table("ActivityQuest", "activity_quest_reward")[newcomer["activity_quest_reward_i_d"]]
    entries = {}
    for aid, row in table("Activity", "activity").items():
        source = row.get("activity_i_d", aid)
        if aid == NEWCOMER:
            title, kind = "新人支援", "newcomer"
            rewards = [{"day": item["days"], "goal": item["achieve_i_d"], "items": item["activity_quest_item"]}
                       for item in newcomer["activity_quest_achieve_items"]]
            rewards.extend({"tier": tier["index"], "required": tier["condition_item_num"],
                            "token": tier["condition_item_i_d"],
                            "items": [{"item_i_d": tier["reward_item_i_d"], "item_num": tier["reward_item_num"]}]}
                           for tier in tiers["activity_quest_reward_item"])
        elif aid == DAILY:
            rewards = [{"goal": item["achieve_i_d"], "items": item["activity_regular_pool_reward_item"]}
                       for item in daily_pool["activity_regular_pool_item"]]
            title, kind = "每日甜点", "daily"
        elif aid == WISH:
            rewards = [{"goal": item["achieve_i_d"], "items": item["activity_regular_pool_reward_item"]}
                       for item in event_pools[wish_pool]["activity_regular_pool_item"]]
            title, kind = "周年许愿", "anniversary_wish"
        elif aid in (5075, 260815130) and row.get("new_activity_type") == 4 and row.get("quest_i_d") == 910101:
            title, kind, rewards = "幽冥庭院", "event_dialogue", []
        elif row.get("new_activity_type") == 3 and source in passes:
            rewards = [{"level": level["level"], "items": level["battle_pass_reward"]}
                       for level in passes[source]["battle_pass_level_ups"]]
            title, kind = "通行证", "battle_pass"
        elif row.get("new_activity_type") == 1 and source in signs:
            rewards = [{"day": item["days"], "items": item.get("activity_sign_reward_item", [])}
                       for item in signs[source]["activity_sign_item"]]
            title, kind = SIGN_TITLES.get(row.get("text_i_d"), "签到"), "sign_in"
        else:
            continue
        entries[aid] = {"id": aid, "name": title, "kind": kind, "opens_at": row.get("opening_time"),
                        "closes_at": row.get("closing_time"), "rewards": rewards}
    return entries


def available():
    return list(_catalog().values())


def _timestamp(value):
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError("Activity date must be null or YYYY-MM-DD HH:MM:SS in UTC+8")
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    if parsed.strftime("%Y-%m-%d %H:%M:%S") != value:
        raise ValueError("Use YYYY-MM-DD HH:MM:SS in UTC+8")


def validate(settings):
    if not isinstance(settings, dict) or settings.keys() != {"events"} or not isinstance(settings["events"], list):
        raise ValueError("Expected an object containing an events array")
    seen = set()
    for override in settings["events"]:
        if not isinstance(override, dict) or override.keys() != {"id", "enabled", "opens_at", "closes_at"}:
            raise ValueError("Each event requires id, enabled, opens_at and closes_at")
        aid = override["id"]
        if type(aid) is not int or aid not in _catalog() or aid in seen or type(override["enabled"]) is not bool:
            raise ValueError("Unknown/duplicate native event or invalid enabled flag")
        seen.add(aid)
        start, end = override["opens_at"], override["closes_at"]
        _timestamp(start)
        _timestamp(end)
        actual = _catalog()[aid]
        if start and actual["opens_at"] and start < actual["opens_at"]:
            raise ValueError("Cannot open before the native calendar")
        if end and actual["closes_at"] and end > actual["closes_at"]:
            raise ValueError("Cannot close after the native calendar")
        if (start or actual["opens_at"] or "") > (end or actual["closes_at"] or "9999"):
            raise ValueError("Activity window is empty")
    return settings


def configure(state, settings=None):
    if settings is not None:
        validate(settings)
    with exclusive_state(state, purpose="profiles") as root:
        root.mkdir(parents=True, exist_ok=True)
        with LocalIdentity(root / "identity.sqlite3")._write() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS private_event_config " + SCHEMA)
            if settings is not None:
                conn.execute("INSERT OR REPLACE INTO private_event_config VALUES(1,?)",
                             (json.dumps(settings, separators=(",", ":")),))
            stored = conn.execute("SELECT settings FROM private_event_config WHERE id=1").fetchone()
            return json.loads(stored[0]) if stored else {"events": []}
