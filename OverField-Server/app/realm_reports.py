"""Private operator review of reports deliberately submitted by players."""
from contextlib import closing
import sqlite3


def setup(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS realm_reports(
         id INTEGER PRIMARY KEY, reporter INTEGER NOT NULL, target INTEGER NOT NULL, category INTEGER NOT NULL,
         channel INTEGER NOT NULL, content TEXT NOT NULL, reason TEXT NOT NULL, photo INTEGER NOT NULL,
         created INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS realm_report_review ON realm_reports(resolved,id DESC)")


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
                    return {"reports":[dict(row) for row in rows]}
                if conn.execute("UPDATE realm_reports SET resolved=1 WHERE id=?", (data["id"],)).rowcount:
                    return {"resolved":data["id"]}
    if listing:
        return {"reports":[]}
    raise FileNotFoundError("Unknown report")
