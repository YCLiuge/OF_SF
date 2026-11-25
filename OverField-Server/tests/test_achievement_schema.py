"""Existing achievements survive the FK repair, including unusual but valid old saves."""
import argparse
from contextlib import closing
import sqlite3
from pathlib import Path
import tempfile

from config import Config
from utils import db
from utils.achievement_schema import repair_achievement_schema


def validate():
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / "game.sqlite"
        with closing(sqlite3.connect(file)) as conn:
            conn.executescript("""
                CREATE TABLE players (player_id INTEGER PRIMARY KEY);
                CREATE TABLE achieve (
                    player_id INTEGER NOT NULL, achieve_id INTEGER NOT NULL, achieve_blob BLOB NOT NULL,
                    PRIMARY KEY(player_id,achieve_id),
                    FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE,
                    FOREIGN KEY(achieve_id) REFERENCES players(player_id) ON DELETE CASCADE
                );
                CREATE INDEX own_achievements ON achieve(achieve_id);
                CREATE VIEW saved_achievements AS SELECT achieve_blob FROM achieve;
                CREATE TABLE observation (val INTEGER);
                CREATE TRIGGER log_new_achievement AFTER INSERT ON achieve BEGIN
                    INSERT INTO observation VALUES (NEW.achieve_id); END;
                INSERT INTO players VALUES(101);
                INSERT INTO achieve(rowid,player_id,achieve_id,achieve_blob) VALUES(51,101,9999,x'0100');
                INSERT INTO achieve(rowid,player_id,achieve_id,achieve_blob) VALUES(63,999,7,x'FF');
            """)
            # A failure before copy-back must leave every original row, trigger and relationship intact.
            conn.execute("CREATE TEMP TABLE of_achievement_copy(block INTEGER)")
            try:
                repair_achievement_schema(conn)
            except sqlite3.OperationalError:
                pass
            else:
                raise AssertionError("Expected the staged copy to be unavailable")
            assert len(conn.execute("PRAGMA foreign_key_list(achieve)").fetchall()) == 2
            conn.execute("DROP TABLE temp.of_achievement_copy")
            repair_achievement_schema(conn)
            repair_achievement_schema(conn)
            assert conn.execute("SELECT rowid,* FROM achieve ORDER BY rowid").fetchall() == [
                (51,101,9999,b"\x01\x00"), (63,999,7,b"\xff")]
            assert conn.execute("SELECT * FROM saved_achievements ORDER BY 1").fetchall() == [(b"\x01\x00",),(b"\xff",)]
            assert conn.execute("SELECT val FROM observation").fetchall() == [(9999,), (7,)]
            assert conn.execute("SELECT name FROM sqlite_schema WHERE name='own_achievements'").fetchone()
            assert conn.execute("PRAGMA foreign_key_check(achieve)").fetchall() == [("achieve",63,"players",0)]
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("INSERT INTO achieve VALUES(101,45000,x'42')")
            assert conn.execute("SELECT val FROM observation ORDER BY rowid DESC LIMIT 1").fetchone() == (45000,)
            try:
                conn.execute("INSERT INTO achieve VALUES(111,1,x'01')")
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("New orphan allowed")
        Config.IN_MEMORY = False
        Config.DB_PATH = str(Path(directory) / "fresh.sqlite")
        db.init()
        try:
            assert db.conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert [(r[2],r[3],r[4]) for r in db.conn.execute("PRAGMA foreign_key_list(achieve)")] == [
                ("players","player_id","player_id")]
        finally:
            db.exit()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources")
    parser.parse_args()
    validate()
    print("PASS legacy achievement FK migration, rollback, orphan preservation, custom schema, and fresh game writer.")
