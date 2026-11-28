"""Human-readable choices for existing owner operations."""
from functools import lru_cache
import json
from pathlib import Path

from realm_reward_catalog import display_names

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


@lru_cache(maxsize=1)
def activity_names():
    return json.loads((RESOURCES / "operator/activity-names.zh.json").read_bytes())


def titled_events(entries):
    names = activity_names()
    result = []
    for row in entries:
        official = names.get(str(row["id"]))
        # Curated labels distinguish original/rerun fireworks and dated passes.
        if row["kind"] == "sakura_fireworks" or row["name"].endswith("期通行证"):
            official = row["name"]
        if not official:
            official = {"activity_gift": "活动礼包", "photo_show": "摄影投稿", "turntable": "扭蛋机"}.get(row["kind"], row["name"])
        result.append({**row, "name": official})
    return result


def titled_plaza(catalog, events):
    editions = {row["id"]: row for row in titled_events(events)}
    activities = []
    for row in catalog["activities"]:
        entry = editions.get(row["id"])
        if entry:
            # Some entrances share a title but open in different seasons.
            name = row["name"] if "（" in row["name"] else entry["name"]
            row = {**row, "name": name, "opens_at": entry["opens_at"]}
        activities.append(row)
    return {**catalog, "activities": activities}


@lru_cache(maxsize=1)
def star_templates():
    """Mirror the world's reward-table indirections; summarize without rolling loot."""
    raw = json.loads((RESOURCES / "data/Reward.json").read_bytes())
    def groups(table):
        return {row["i_d"]: row[table + "_group"] for row in raw[table]["datas"] if row.get(table + "_group")}

    worlds, pools, items = (groups(key) for key in ("world_level_pool", "reward_pool", "reward_item_pool"))
    names = display_names()
    result = []
    for key in sorted(worlds.keys() | pools.keys() | items.keys()):
        pool_ids = {row["pool_i_d"] for row in worlds.get(key, ())} or {key}
        group_ids = {row["reward_pool_group_i_d"] for pool in pool_ids
                     for row in pools.get(pool, [{"reward_pool_group_i_d": pool}])}
        amounts = {}
        for group in sorted(group_ids):
            for reward in items.get(group, ()):
                high = reward.get("item_max_count", reward.get("item_min_count", 1))
                if high <= 0 or not reward.get("item_i_d"):
                    continue
                iid = reward["item_i_d"]
                low = reward.get("item_min_count", 1)
                if iid in amounts:
                    low = min(low, amounts[iid][0])
                    high = max(high, amounts[iid][1])
                amounts[iid] = (low, high)
        if not amounts:
            continue
        labels = []
        for iid, (low, high) in list(amounts.items())[:3]:
            name = names.get(str(iid), f"未命名物品（{iid}）")
            count = str(low) if low == high else f"{low}–{high}"
            labels.append(f"{name} ×{count}")
        title = "、".join(labels)
        if len(amounts) > len(labels):
            title += f" 等 {len(amounts)} 种"
        result.append({"id": key, "name": title})
    return result


def reward_names(entries):
    """Resolve just the item keys shown in the activity or blessing catalog."""
    ids = set()
    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in ("item_i_d", "ticket", "token") and isinstance(child, int):
                    ids.add(str(child))
                elif key == "currency_ids":
                    ids.update(map(str, child))
                else:
                    visit(child)
    visit(entries)
    names = display_names()
    return {key: names[key] for key in ids if key in names}
