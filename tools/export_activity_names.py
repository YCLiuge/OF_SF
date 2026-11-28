"""Export the small native Chinese edition-name index used by the operator console."""
import argparse
import hashlib
import json
from pathlib import Path


RELATIVE = "resources/operator/activity-names.zh.json"


def encode(string_table, resources):
    def rows(file, table):
        return json.loads((resources / (file + ".json")).read_bytes())[table]["datas"]

    translations = json.loads(string_table.read_bytes())
    def labels(table):
        return {row["i_d"]: row["text"][0].strip() for row in translations[table]["datas"]
                if row.get("text") and row["text"][0].strip()}

    titles = labels("string_activity")
    wheels = labels("string_activity_turntable")
    shops = labels("string_shop")
    wheel_names = {r["i_d"]: wheels.get(r.get("text_i_d")) for r in rows("ActivityTurntable", "activity_turntable")}
    gifts = {r["i_d"]: shops.get(r.get("shop_i_d")) for r in rows("ActivityGift", "activity_gift")}
    result = {}
    for entry in rows("Activity", "activity"):
        aid = entry["i_d"]
        source = entry.get("activity_i_d", aid)
        name = titles.get(entry.get("text_i_d"))
        if entry.get("new_activity_type") == 6:
            name = " · ".join(dict.fromkeys(n for n in (name, wheel_names.get(source)) if n))
        elif entry.get("new_activity_type") == 2:
            name = name or gifts.get(source)
        if name:
            result[str(aid)] = name
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("string_table", type=Path)
    cli.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    args = cli.parse_args()
    data = encode(args.string_table, args.server / "resources/data")
    expected = json.loads((args.server / "resource-sources.json").read_bytes())["files"][RELATIVE]["sha256"]
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("Activity name index differs from the server manifest")
    path = args.server / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
