"""Read-only, paginated selection of claimable items for the operator console."""
from functools import lru_cache
import json
from pathlib import Path

import realm_codes
import realm_mail

GROUPS = {
    "货币与补给": (9, 15, 19, 27, 29, 30, 32, 35, 38, 45),
    "武器与强化": (2, 3, 4, 11, 18, 36, 37),
    "探索员与映像": (5, 7, 25, 40, 46),
    "时装与外观": (21, 22, 26, 31, 33, 39, 42, 43, 44),
    "生活与采集": (10, 13, 14, 20, 53), "家具": (34,),
    "探索与收藏": (6, 8, 12, 23, 24, 28), "宠物": (55, 56, 57, 58),
    "礼盒": (1,),
}
PAGE_SIZE = 24


@lru_cache(maxsize=1)
def display_names():
    return json.loads((Path(__file__).resolve().parents[1] / "resources/operator/item-names.zh.json").read_bytes())


def label_history(result, ids):
    names = display_names()
    result["item_names"] = {key: names.get(key, "未命名物品") for key in set(map(str, ids))}
    return result


@lru_cache(maxsize=1)
def catalog():
    names = display_names()
    groups = {tag: name for name, tags in GROUPS.items() for tag in tags}
    return tuple((key, names.get(str(key), "未命名物品"), groups.get(tag, "其他"),
                  realm_mail.reward_limit(key, tag), realm_codes.reward_limit(tag))
                 for key, tag in sorted(realm_codes.tags().items()))


def search(data):
    query = data.get("query", "").strip().casefold()
    lookup_id = query.isascii() and query.isdecimal()
    group = data.get("group")
    index = 3 if data["channel"] == "mail" else 4
    offset = data.get("offset", 0)
    items, groups = [], set()
    total = 0
    for row in catalog():
        key, name, category = row[:3]
        if not row[index]:
            continue
        groups.add(category)
        if group and group != category:
            continue
        if lookup_id and query != str(key) or not lookup_id and query not in name.casefold():
            continue
        if offset <= total < offset + PAGE_SIZE:
            items.append({"id": key, "name": name, "group": category, "maximum": row[index]})
        total += 1
    return {"items": items, "groups": sorted(groups), "total": total, "page_size": PAGE_SIZE}
