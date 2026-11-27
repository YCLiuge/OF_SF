"""Extract just the Chinese item display names needed by the owner console."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "OverField-Server"
RELATIVE = "resources/operator/item-names.zh.json"


def export(string_table, server, item_table=None):
    files = json.loads((server / "resource-sources.json").read_bytes())["files"]
    items = (item_table or server / "resources/data/Item.json").read_bytes()
    if sha256(items).hexdigest() != files["resources/data/Item.json"]["sha256"]:
        raise ValueError("Item.json differs from the server resource manifest")
    texts = json.loads(string_table.read_bytes())["string_item_text"]["datas"]
    strings = {row["i_d"]: row["text"][0].strip() for row in texts if row.get("text") and row["text"][0]}
    names = {str(row["i_d"]): strings[row["text_i_d"]] for row in json.loads(items)["item"]["datas"]
             if strings.get(row.get("text_i_d"))}
    raw = json.dumps(names, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    if sha256(raw).hexdigest() != files[RELATIVE]["sha256"]:
        raise ValueError("Simplified Chinese strings do not match this server build")
    path = server / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    print(f"{len(names)} verified Chinese item names written to {path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("string_table", type=Path)
    parser.add_argument("--items", type=Path, help="Matching Item.json from an extracted server package")
    parser.add_argument("--server", type=Path, default=ROOT)
    args = parser.parse_args()
    export(args.string_table, args.server, args.items)
