"""Owner settings for native 1.1.12 recruit banners."""
from functools import lru_cache
import json
from pathlib import Path

from local_identity import LocalIdentity
from state_lock import exclusive_state


@lru_cache(maxsize=1)
def _catalog():
    path = Path(__file__).resolve().parents[1] / "resources/data/Gacha.json"
    with path.open(encoding="utf-8") as file:
        gacha = json.load(file)
    groups = {r["i_d"]: [i["free_gacha_pool_i_d"] for i in r["items"]] for r in gacha["pool"]["datas"]}
    return {r["i_d"]: {"id": r["i_d"], "groups": groups[r["method1_i_d"]],
                       "pity_group": r["big_guarantee_pool_i_d"], "featured": r.get("character_i_d1", 0),
                       "opening": r.get("opening_time"), "closing": r.get("closing_time"),
                       "ticket": r["consume_item1_i_d"]}
            for r in gacha["info"]["datas"] if r["i_d"] in (2000, 2002) or r.get("new_u_i_gacha_type") == 3
            if r.get("method1_i_d") in groups and r.get("big_guarantee_pool_i_d") in groups[r["method1_i_d"]]}


def available():
    return list(_catalog().values())


def featured_pool(pool_id):
    banner = _catalog().get(pool_id)
    if not banner or banner["pity_group"] in (100, 200, 300, 400):
        raise ValueError("Expected a native featured banner ID")
    weights = {"100": 7900, "200": 1200, "300": 700, "400": 100,
               str(banner["pity_group"]): 100}
    pool = {"id": pool_id, "enabled": True, "pity": 70, "weights": weights}
    validate({"pools": [pool]})
    return pool


def defaults():
    permanent = {"id": 2000, "enabled": True, "pity": 70, "sr_pity": 10,
                 "weights": {"100": 9220, "200": 80, "300": 600, "400": 100}}
    featured_ids = (3000, 3001, 3023, 203014, 203015, 3002)
    return {"pools": [permanent] + [featured_pool(key) for key in featured_ids]}


def validate(settings):
    if not isinstance(settings, dict) or settings.keys() != {"pools"} or not isinstance(settings["pools"], list):
        raise ValueError("Expected an object containing the pools array")
    known = _catalog()
    seen = set()
    for row in settings["pools"]:
        required = {"id", "enabled", "pity", "weights"}
        if not isinstance(row, dict) or not required <= row.keys() <= (required | {"sr_pity"}):
            raise ValueError("Each pool requires id, enabled, pity and weights")
        pid, weights = row["id"], row["weights"]
        if type(pid) is not int or pid not in known or pid in seen or type(row["enabled"]) is not bool:
            raise ValueError("Invalid or duplicate native pool")
        seen.add(pid)
        if type(row["pity"]) is not int or not 1 <= row["pity"] <= 1000:
            raise ValueError("Pity must be 1–1000")
        if "sr_pity" in row and (pid != 2000 or type(row["sr_pity"]) is not int or not 0 <= row["sr_pity"] <= 1000):
            raise ValueError("sr_pity is an integer from 0 to 1000 for permanent banner 2000 only")
        valid = {str(k) for k in known[pid]["groups"]}
        if not isinstance(weights, dict) or not weights or not weights.keys() <= valid:
            raise ValueError("Unknown reward group for this native pool")
        if any(type(v) is not int or not 0 <= v <= 10000 for v in weights.values()) or sum(weights.values()) != 10000:
            raise ValueError("Reward weights must be integer basis points totaling 10000")
    return settings


SCHEMA = "(id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)"


def configure(state, settings=None, *, enable_featured=None):
    if settings is not None:
        validate(settings)
    with exclusive_state(state, purpose="profiles") as root:
        root.mkdir(parents=True, exist_ok=True)
        with LocalIdentity(root / "identity.sqlite3")._write() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS private_gacha_config " + SCHEMA)
            stored = conn.execute("SELECT settings FROM private_gacha_config WHERE id=1").fetchone()
            current = json.loads(stored[0]) if stored else defaults()
            if enable_featured is not None:
                template = featured_pool(enable_featured)
                match = next((p for p in current["pools"] if p["id"] == enable_featured), None)
                if match is None:
                    current["pools"].append(template)
                else:
                    match["enabled"] = True
                settings = current
            if settings is None:
                return current
            conn.execute("INSERT OR REPLACE INTO private_gacha_config VALUES(1,?)",
                         (json.dumps(settings, separators=(",", ":")),))
            return settings
