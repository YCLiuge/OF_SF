"""Persist the per-result grant shown to a private player alongside the native draw history."""
import json


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS private_gacha_rewards (
        record_id INTEGER PRIMARY KEY REFERENCES gacha_record(id) ON DELETE CASCADE,
        origin TEXT NOT NULL CHECK(origin IN ('draw','tutorial','legacy_repair')),
        is_new INTEGER NOT NULL CHECK(is_new IN (0,1)),
        extras TEXT NOT NULL)""")


def store(conn, record_id, origin, is_new, credits):
    # Quantities here are per result. PackNotice carries absolute balances.
    encoded = json.dumps(list(credits), separators=(",", ":"))
    conn.execute("INSERT INTO private_gacha_rewards(record_id,origin,is_new,extras) VALUES(?,?,?,?)",
                 (record_id, origin, int(is_new), encoded))


def record(db, player, banner, delta, origin="draw"):
    if not db.conn.in_transaction:
        raise RuntimeError("Recruit reward and history must share the gameplay transaction")
    record_id = db.add_gacha_record(player, banner, delta.main_item.item_id)
    store(db.conn, record_id, origin, delta.main_item.is_new,
          ((item.item_id, item.base_item.num) for item in (*delta.transformed_item, *delta.extras)))
