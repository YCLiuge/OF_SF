"""Owner-issued realm reward codes. Definitions travel with the identity backup."""
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3

from local_identity import LocalIdentity
from realm_events import _timestamp
from state_lock import exclusive_state

VALID = re.compile(r"[A-Z0-9_-]{1,48}\Z", re.ASCII)
KIND_COLUMN = "TEXT NOT NULL DEFAULT 'redemption' CHECK (kind IN ('redemption', 'inherit'))"
SCHEMA = f"""(code TEXT PRIMARY KEY, rewards TEXT NOT NULL, quota INTEGER NOT NULL,
             expiry TEXT, enabled INTEGER NOT NULL, kind {KIND_COLUMN}) WITHOUT ROWID"""


def ensure_schema(conn, *, attached=False):
    schema = "authority" if attached else "main"
    table = f"{schema}.realm_reward_codes"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} " + SCHEMA)
    columns = conn.execute(f"PRAGMA {schema}.table_info(realm_reward_codes)")
    if all(column[1] != "kind" for column in columns):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN kind {KIND_COLUMN}")


def normalize(code):
    value = code.strip()
    if not value.isascii() or not VALID.fullmatch(value.upper()):
        raise ValueError("Use 1 to 48 ASCII letters, numbers, underscores or hyphens")
    return value.upper()


@lru_cache(maxsize=1)
def tags():
    path = Path(__file__).resolve().parents[1]/"resources/data/Item.json"
    return {r["i_d"]:r["new_bag_item_tag"] for r in json.loads(path.read_bytes())["item"]["datas"]}


def manage(state, name, params):
    if name=="codes.list" and not (state/"identity.sqlite3").is_file():
        return {"codes":[]}
    with exclusive_state(state,purpose="profiles") as root:
        root.mkdir(parents=True,exist_ok=True)
        with LocalIdentity(root/"identity.sqlite3")._write() as conn:
            ensure_schema(conn)
            if name=="codes.list":
                return {"codes":[dict(code=c,kind=k,rewards=json.loads(r),quota=q,expires_at=t,enabled=bool(e))
                    for c,r,q,t,e,k in conn.execute("SELECT code,rewards,quota,expiry,enabled,kind FROM realm_reward_codes ORDER BY code")]}
            code = normalize(params["code"])
            if name=="codes.withdraw":
                if not conn.execute("UPDATE realm_reward_codes SET enabled=0 WHERE code=?", (code,)).rowcount:
                    raise ValueError("Unknown reward code")
                return {"code":code,"enabled":False}
            _timestamp(params.get("expires_at"))
            amounts = {}
            for item in params["rewards"]:
                key, num = item["item"],item["quantity"]
                tag = tags().get(key)
                if tag is None or tag in (1,17,41,47,48,49,50,56) or key in amounts:
                    raise ValueError("Unknown, repeated or unsupported reward template")
                if tag in (2,3,5,7,53) and num>20 or tag==21 and num!=1:
                    raise ValueError("At most 20 separate instances or one outfit per template")
                amounts[key]=num
            if not amounts:
                raise ValueError("Supply at least one reward")
            try:
                conn.execute("INSERT INTO realm_reward_codes(code,rewards,quota,expiry,kind,enabled) VALUES(?,?,?,?,?,1)",
                             (code,json.dumps(amounts),params["quota"],params.get("expires_at"),
                              params.get("kind","redemption")))
            except sqlite3.IntegrityError as exc:
                raise ValueError("This reward code has already been issued") from exc
            return {"code":code,"enabled":True}
