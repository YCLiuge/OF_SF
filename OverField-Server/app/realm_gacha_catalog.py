"""Chinese display labels and local artwork for the operator recruit catalog."""
from functools import lru_cache
import json
from pathlib import Path
import re
from zipfile import ZipFile

from realm_reward_catalog import display_names


ROOT = Path(__file__).resolve().parents[1] / "resources/operator"
GROUP_NAMES = {
    100: "SR 映像", 200: "SSR 映像", 210: "SSR 映像", 220: "精选 SSR 映像",
    300: "SR 探索员", 400: "SSR 探索员", 500: "精选 SSR 探索员", 2001: "粉红偶像 UP 探索员",
    40000: "染料", 40001: "家具", 40002: "紫色武器", 40003: "金色武器", 40004: "时装",
}


@lru_cache(maxsize=1)
def pack():
    path = ROOT / "gacha-cards.zip"
    if not path.is_file():
        return {}, {}
    with ZipFile(path) as archive:
        cards = json.loads(archive.read("cards.json"))
        art = {"/admin/recruit-art/" + name: ("image/webp", archive.read(name))
               for name in archive.namelist() if re.fullmatch(r"[1-9][0-9]*\.webp", name)}
    return cards, art


def decorate(banners):
    cards, _ = pack()
    try:
        items = display_names()
    except FileNotFoundError:
        items = {}
    rows = []
    for row in banners:
        card = cards.get(str(row["id"]), {})
        labels = {}
        for group in row["groups"]:
            if group in GROUP_NAMES:
                labels[str(group)] = GROUP_NAMES[group]
            elif group == row["pity_group"] and card.get("actor"):
                labels[str(group)] = "UP · " + card["actor"]
            else:
                labels[str(group)] = "其他奖励组 " + str(group)
        rows.append({**row, **card, "group_names": labels, "ticket_name": items.get(str(row["ticket"]), "招募券")})
    return rows
