import json
from pathlib import Path

res = {}
def init(resources):
    directory = Path(resources) / "data"
    loaded = {f.stem:json.loads(f.read_bytes()) for f in sorted(directory.glob("*.json"))}
    if not {"ScenesConfigAsset","Quest","Character","Flag","Item"} <= loaded.keys():
        raise ValueError("Missing gameplay resources")
    res.clear()
    res.update(loaded)
