"""Transactional lookup of played names, including game.sqlite edited by ordinary SQLite tools."""
import unicodedata

from social_query import name_digest


REVISION="1/"+unicodedata.unidata_version
# Empty and NULL player names are ignored by the historical rename check. All other
# invalid historical names have an empty digest; valid native name digests are 32 bytes.
def strict(value):
    return b"\0" if value is None or value=="" else name_digest(value)


def signup(value):
    # Registration historically compared stripped NFKC/casefold even for legacy names
    # the native name panel would reject (such as an over-length spelling).
    return unicodedata.normalize("NFKC",value.strip()).casefold() if isinstance(value,str) else None


def functions(conn):
    conn.create_function("of_nick_digest",1,strict,deterministic=True)
    conn.create_function("of_nick_signup",1,signup,deterministic=True)


def setup(conn):
    functions(conn)
    conn.execute("SAVEPOINT of_nickname_schema")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS of_players_exact_name ON players(player_name) WHERE player_name IS NOT NULL")
        conn.execute("CREATE TABLE IF NOT EXISTS private_player_names(uid INTEGER PRIMARY KEY, raw, digest BLOB, signup TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS private_player_name_digest ON private_player_names(digest)")
        conn.execute("CREATE INDEX IF NOT EXISTS private_player_name_signup ON private_player_names(signup)")
        conn.execute("CREATE TABLE IF NOT EXISTS private_player_name_version(id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL)")
        for event,suffix in (("INSERT","insert"),("UPDATE OF player_id,player_name","update")):
            changed="" if suffix=="insert" else " WHEN OLD.player_id IS NOT NEW.player_id OR OLD.player_name IS NOT NEW.player_name"
            old="" if suffix=="insert" else "DELETE FROM private_player_names WHERE uid=OLD.player_id AND OLD.player_id!=NEW.player_id;"
            # Persistent triggers contain only built-in SQL. A stock SQLite client can
            # edit, import or integrity-check the database without loading application code.
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS of_names_{suffix} AFTER {event} ON players{changed} BEGIN
                {old} INSERT INTO private_player_names(uid,raw) VALUES(NEW.player_id,NEW.player_name)
                ON CONFLICT(uid) DO UPDATE SET raw=excluded.raw,digest=NULL,signup=NULL
                WHERE private_player_names.raw IS NOT excluded.raw; END""")
            # Game writes maintain both indexes immediately, in the originating transaction.
            # The raw equality guard above makes the ordering of the two triggers immaterial.
            conn.execute(f"""CREATE TEMP TRIGGER IF NOT EXISTS of_names_cached_{suffix} AFTER {event} ON main.players{changed} BEGIN
                INSERT INTO private_player_names(uid,raw,digest,signup)
                VALUES(NEW.player_id,NEW.player_name,of_nick_digest(NEW.player_name),of_nick_signup(NEW.player_name))
                ON CONFLICT(uid) DO UPDATE SET raw=excluded.raw,digest=excluded.digest,signup=excluded.signup; END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS of_names_delete AFTER DELETE ON players BEGIN
            DELETE FROM private_player_names WHERE uid=OLD.player_id; END""")
        row=conn.execute("SELECT revision FROM private_player_name_version WHERE id=1").fetchone()
        if not row or row[0]!=REVISION:
            conn.execute("DELETE FROM private_player_names")
            conn.execute("""INSERT INTO private_player_names SELECT player_id,player_name,
                of_nick_digest(player_name),of_nick_signup(player_name) FROM players""")
            conn.execute("INSERT OR REPLACE INTO private_player_name_version VALUES(1,?)",(REVISION,))
        else:
            conn.execute("""UPDATE private_player_names SET digest=of_nick_digest(raw),signup=of_nick_signup(raw)
                WHERE digest IS NULL""")
    except BaseException:
        conn.execute("ROLLBACK TO of_nickname_schema")
        raise
    finally:
        conn.execute("RELEASE of_nickname_schema")


# Names changed by external SQLite clients remain immediately searchable, even before
# restart. Only these pending rows invoke Unicode normalization on the reader.
DIGEST_MATCH="""SELECT uid FROM private_player_names WHERE digest=?
    UNION ALL SELECT uid FROM private_player_names WHERE digest IS NULL AND of_nick_digest(raw)=?"""
SIGNUP_MATCH="""SELECT uid FROM private_player_names WHERE signup=?
    UNION ALL SELECT uid FROM private_player_names WHERE digest IS NULL AND of_nick_signup(raw)=?"""


def find(conn,token,*,exclude=None,friends=False):
    visible=" AND EXISTS(SELECT 1 FROM private_slots s WHERE s.player_id=n.uid)" if friends else ""
    result=conn.execute(f"SELECT uid FROM ({DIGEST_MATCH}) n WHERE uid IS NOT ?{visible} ORDER BY uid LIMIT 1",
                        (token,token,exclude)).fetchone()
    return result[0] if result else None


def blocks_rename(conn,pid,name):
    existing=find(conn,name_digest(name),exclude=pid)
    invalid=find(conn,b"",exclude=pid)
    # Match the previous rowid-order first result when an invalid historical row and a
    # duplicate coexist. Authority reservations are still checked before this function.
    if invalid is not None and (existing is None or invalid<existing):
        raise ValueError("Invalid stored player name")
    return existing is not None


def played_by_other(conn,slot,key):
    functions(conn)
    schema=conn.execute("SELECT 1 FROM sqlite_schema WHERE name='private_player_name_version'").fetchone()
    current=schema and conn.execute("SELECT 1 FROM private_player_name_version WHERE id=1 AND revision=?",(REVISION,)).fetchone()
    if not current:
        # A stopped pre-migration realm can still be opened by the independent account service.
        hits="SELECT player_id AS uid FROM players WHERE player_name!='' AND of_nick_signup(player_name)=?"
        params=(key,slot)
    else:
        hits=SIGNUP_MATCH
        params=(key,key,slot)
    return conn.execute(f"""SELECT 1 FROM ({hits}) n LEFT JOIN private_slots s ON s.player_id=n.uid
        WHERE s.slot IS NOT ? LIMIT 1""",params).fetchone() is not None
