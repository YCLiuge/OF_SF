"""Build the 1.1.12 random drop allowlist from locally exported retail tables."""
import argparse
import json
from pathlib import Path


def collect(data):
    def table(file, name):
        return json.loads((data / (file + ".json")).read_bytes())[name]["datas"]

    weapons = {r["item_i_d"] for r in table("Weapon", "weapon")
               if r.get("new_weapon_system_type") in (1, 2, 3, 4, 5) and r.get("item_i_d")}
    armor = {r["item_i_d"] for r in table("Armor", "armor") if r.get("item_i_d")}
    icons = {r["i_d"]: r.get("asset_full_name", []) for r in table("UI", "icon")}
    texts = {r["i_d"]: r.get("text", []) for r in table("String_Simplified", "string_item_text")}
    eligible = []
    for row in table("Item", "item"):
        item = row["i_d"]
        tag = row.get("new_bag_item_tag")
        if not ((tag == 2 and item in weapons) or (tag == 3 and item in armor)):
            continue
        paths = icons.get(row.get("icon_i_d"))
        if not paths or not all(isinstance(p, str) and p.strip() and not p.strip().endswith(("/", "\\")) for p in paths):
            continue
        labels = texts.get(row.get("text_i_d"))
        if labels and isinstance(labels[0], str):
            name = labels[0].strip().casefold()
            if name and not name.startswith(("测试", "test")):
                eligible.append(item)
    return {"equipment_ids": sorted(eligible)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(collect(args.data), separators=(",", ":")) + "\n", encoding="utf-8")
