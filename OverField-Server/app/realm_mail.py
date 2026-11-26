"""Owner mail is published atomically with its audience snapshot in the identity database."""
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
import time
from threading import Lock
from uuid import UUID

from local_identity import LocalIdentity
from realm_accounts import username_spelling
from realm_codes import tags
from state_lock import exclusive_state


_publish_lock = Lock()


def schema(db, *, attached=False):
    prefix = "authority." if attached else ""
    db.execute(f"""CREATE TABLE IF NOT EXISTS {prefix}realm_mail_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
        payload TEXT NOT NULL, issued INTEGER NOT NULL, recipients INTEGER NOT NULL DEFAULT 0)""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS {prefix}realm_mail_recipients (
        slot TEXT NOT NULL, batch INTEGER NOT NULL, PRIMARY KEY(slot,batch)) WITHOUT ROWID""")
    db.execute(f"CREATE INDEX IF NOT EXISTS {prefix}realm_mail_scope ON realm_mail_batches(scope,id)")


@lru_cache(maxsize=1)
def warehouse_weapons():
    path = Path(__file__).resolve().parents[1]/"resources/data/Weapon.json"
    # Several display-only weapon templates lack a corresponding inventory item.
    return frozenset(row["i_d"] for row in json.loads(path.read_bytes())["weapon"]["datas"]
                     if row.get("item_i_d") == row["i_d"])


def letter(data):
    try:
        request = str(UUID(data["request_id"]))
    except (ValueError, AttributeError):
        raise ValueError("投递批次号须为 UUID。") from None
    scope = data["scope"]
    if scope not in ("account", "existing", "ongoing"):
        raise ValueError("请选择投递范围。")
    user = data.get("username", "")
    if (scope == "account") != bool(user):
        raise ValueError("指定账号时须填写登录账号，全服投递请留空。")
    payload = {"scope": scope, "username": username_spelling(user).casefold() if user else ""}
    for key, limit in (("sender", 50), ("title", 100), ("body", 2000)):
        value = data.get(key, "区服管理" if key == "sender" else "").strip()
        if not value or len(value) > limit:
            raise ValueError("邮件发件人、标题、正文各限 50、100、2000 个字符。")
        payload[key] = value
    days = data.get("days", 30)
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("有效期须为 1 至 365 天。")
    payload["days"] = days
    items = {}
    for entry in data.get("rewards", []):
        item, quantity = entry["item"], entry["quantity"]
        tag = tags().get(item)
        if type(item) is not int or tag is None or tag in {0,7,17,41,47,48,49,50,56}:
            raise ValueError(f"附件模板 {item} 未提供可领取的物品。")
        if tag == 2 and item not in warehouse_weapons():
            raise ValueError(f"模板 {item} 不是可入库的武器。")
        limit = 20 if tag in (2,3,5,53) else 1 if tag == 21 else 1000000
        if type(quantity) is not int or not 1 <= quantity <= limit or item in items:
            raise ValueError(f"模板 {item} 须只填一行，数量范围为 1 至 {limit}。")
        items[item] = quantity
    payload["rewards"] = sorted(items.items())
    return request, payload


def receipt(row):
    number, key, payload, issued, recipients = row
    data = json.loads(payload)
    return dict(id=number, request_id=key, title=data["title"], scope=data["scope"], username=data["username"],
                sender=data["sender"], body=data["body"], rewards=[{"item":i,"quantity":n} for i,n in data["rewards"]],
                issued=issued, expires=issued + data["days"] * 86400, recipients_at_issue=recipients)


def recent(state, data):
    path = Path(state)/"identity.sqlite3"
    if not path.is_file():
        return {"batches":[]}
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='realm_mail_batches'").fetchone():
            return {"batches":[]}
        rows = db.execute("""SELECT id,request_id,payload,issued,recipients FROM realm_mail_batches
            WHERE id < ? ORDER BY id DESC LIMIT ?""", (data.get("before", 2**63-1), data.get("limit", 50)))
        return {"batches":[receipt(row) for row in rows]}


def send(state, data):
    key, message = letter(data)
    raw = json.dumps(message, ensure_ascii=False, separators=(",",":"), sort_keys=True)
    with _publish_lock, exclusive_state(state, purpose="profiles") as root:
        root.mkdir(parents=True, exist_ok=True)
        identity = LocalIdentity(root/"identity.sqlite3")
        with identity._write() as db:
            schema(db)
            query = "SELECT id,request_id,payload,issued,recipients FROM realm_mail_batches WHERE request_id=?"
            existing = db.execute(query, (key,)).fetchone()
            if existing:
                if existing[2] != raw:
                    raise ValueError("该批次号已用于其他内容，请核对投递记录。")
                return receipt(existing)
            scope = message["scope"]
            now = int(time.time())
            number = db.execute("""INSERT INTO realm_mail_batches(request_id,scope,payload,issued)
                VALUES(?,?,?,?)""", (key,scope,raw,now)).lastrowid
            if scope == "account":
                if not db.execute("SELECT 1 FROM sqlite_schema WHERE name='realm_accounts'").fetchone():
                    raise ValueError("未找到指定登录账号。")
                count = db.execute("""INSERT INTO realm_mail_recipients(slot,batch)
                    SELECT slot,? FROM realm_accounts WHERE username=? COLLATE NOCASE""",
                    (number,message["username"])).rowcount
                if count != 1:
                    raise ValueError("未找到指定登录账号。")
            elif scope == "existing":
                count = db.execute("INSERT INTO realm_mail_recipients(slot,batch) SELECT slot,? FROM profiles",
                                   (number,)).rowcount
            else:
                count, = db.execute("SELECT count(*) FROM profiles").fetchone()
            db.execute("UPDATE realm_mail_batches SET recipients=? WHERE id=?", (count,number))
            return receipt((number,key,raw,now,count))

