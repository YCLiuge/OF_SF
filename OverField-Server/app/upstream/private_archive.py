"""Player-scoped in-game preferences. Called inside the realm gameplay transaction."""
from utils import db

MAX_KEYS = 1024
MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 32768


def _ensure_table():
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS private_archive (
            player_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (player_id, key)
        ) WITHOUT ROWID
    """)


def get_value(player_id, key):
    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise ValueError("Archive key too long")
    _ensure_table()
    row = db.conn.execute(
        "SELECT value FROM private_archive WHERE player_id = ? AND key = ?", (player_id, key)
    ).fetchone()
    if row is not None:
        return row[0]
    # Earlier community builds used the same role's player_kv archive namespace.
    old = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='archive' AND k=?",
                          (player_id, key)).fetchone()
    if old:
        value = old[0]
        if isinstance(value, bytes) and len(value) <= MAX_VALUE_BYTES:
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                value = None
        if isinstance(value, str) and len(value.encode("utf-8")) <= MAX_VALUE_BYTES:
            return value
    return "1" if key == "BlessingTreeTutotal" else ""


def set_value(player_id, key, value):
    if len(key.encode("utf-8")) > MAX_KEY_BYTES or len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Archive key or value too long")
    _ensure_table()
    changed = db.conn.execute(
        "UPDATE private_archive SET value = ? WHERE player_id = ? AND key = ?", (value, player_id, key)
    ).rowcount
    if changed:
        return
    count, = db.conn.execute("SELECT count(*) FROM private_archive WHERE player_id = ?", (player_id,)).fetchone()
    if count >= MAX_KEYS:
        raise ValueError("Archive entry limit reached")
    db.conn.execute("INSERT INTO private_archive(player_id, key, value) VALUES (?, ?, ?)", (player_id, key, value))
