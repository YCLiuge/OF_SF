"""Private operator review of reports deliberately submitted by players."""
from contextlib import closing
import sqlite3


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS realm_reports(
         id INTEGER PRIMARY KEY, reporter INTEGER NOT NULL, target INTEGER NOT NULL, category INTEGER NOT NULL,
         channel INTEGER NOT NULL, content TEXT NOT NULL, reason TEXT NOT NULL, photo INTEGER NOT NULL,
         created INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS realm_report_review ON realm_reports(resolved,id DESC)")


def _player_names(conn, uids):
    if not uids:
        return {}
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE name IN ('players','realm_public_uids')")}
    if "players" not in tables:
        return {}
    marks = ",".join("?" for _ in uids)
    if "realm_public_uids" not in tables:
        return dict(conn.execute(f"SELECT player_id,player_name FROM players WHERE player_id IN ({marks})", uids))
    # Reports store the public UID, which can differ from an imported character's local ID.
    return dict(conn.execute(f"""
        SELECT u.uid,p.player_name FROM realm_public_uids u
        JOIN players p USING(player_id) WHERE u.uid IN ({marks})
        UNION ALL SELECT p.player_id,p.player_name FROM players p WHERE p.player_id IN ({marks})
        AND NOT EXISTS (SELECT 1 FROM realm_public_uids u WHERE u.uid=p.player_id)
        AND NOT EXISTS (SELECT 1 FROM realm_public_uids u WHERE u.player_id=p.player_id)
        """, [*uids,*uids]))


def manage(state,name,data):
    path = (state/"game.sqlite").resolve()
    listing = name=="reports.list"
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+("?mode=ro" if listing else "?mode=rw"),uri=True,timeout=3)) as conn,conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='realm_reports'").fetchone():
                if listing:
                    conn.row_factory=sqlite3.Row
                    rows=conn.execute("""SELECT id,reporter,target,category,channel,content,reason,photo,created,resolved
                            FROM realm_reports WHERE resolved=? AND id<? ORDER BY id DESC LIMIT ?""",
                            (int(data.get("resolved",False)),data.get("before",2**63-1),data.get("limit",30)))
                    reports = [dict(row) for row in rows]
                    names = _player_names(conn, sorted({r[k] for r in reports for k in ("reporter","target")}))
                    for report in reports:
                        report["reporter_name"] = names.get(report["reporter"])
                        report["target_name"] = names.get(report["target"])
                        report["photo_text"] = str(report["photo"]) if report["photo"] else ""
                    return {"reports":reports}
                if conn.execute("UPDATE realm_reports SET resolved=1 WHERE id=?", (data["id"],)).rowcount:
                    return {"resolved":data["id"]}
    if listing:
        return {"reports":[]}
    raise FileNotFoundError("Unknown report")
