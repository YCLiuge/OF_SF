"""Extract active character spell levels and their exact material costs from PC configuration."""
import argparse
import json
from pathlib import Path


def collect(data):
    characters = json.loads((data / "Character.json").read_bytes())["character"]["datas"]
    spell = json.loads((data / "Spell.json").read_bytes())
    active = {sid for row in characters for field in ("spell_i_ds", "ex_spell_i_ds") for sid in row.get(field, ())}
    steps = {}
    for row in spell["spell_info"]["datas"]:
        sid = row["i_d"]
        if sid in active:
            levels = sorted(row["spell_configure_items"], key=lambda entry: entry["level"])
            if [entry["level"] for entry in levels] != list(range(1, len(levels) + 1)):
                raise ValueError(f"Non-contiguous spell levels for {sid}")
            steps[sid] = [entry.get("cost_i_d", 0) for entry in levels]
    if set(steps) != active:
        raise ValueError("Active skill missing from Spell")
    costs = {}
    for row in spell["spell_level_up_info"]["datas"]:
        materials = {}
        for group in row["spell_level_up_info_item"]:
            for item, count in zip(group["item_i_d"], group["num"], strict=True):
                if count <= 0:
                    raise ValueError("Expected a positive material cost")
                materials[item] = materials.get(item, 0) + count
        if not materials:
            raise ValueError("No materials in a configured upgrade")
        costs[row["i_d"]] = materials
    if {cid for levels in steps.values() for cid in levels if cid} - costs.keys():
        raise ValueError("Missing cost group")
    return {"steps": steps, "costs": costs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_bytes((json.dumps(collect(args.data), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
