"""Compatibility of indexed nicknames with existing, externally edited and conflicting saves."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import player_name_index as names
from local_identity import _name
from social_query import name_digest


def run(root):
    path=root/"game.sqlite"
    with closing(sqlite3.connect(path)) as manager,closing(sqlite3.connect(path)) as external:
        manager.executescript("""CREATE TABLE players(player_id INTEGER PRIMARY KEY,player_name TEXT);
            CREATE TABLE private_slots(slot TEXT PRIMARY KEY,player_id INTEGER UNIQUE NOT NULL) WITHOUT ROWID;""")
        spellings=[(0,"before"),(1,None),(2,""),(3,"  ＦＥＥ  "),(4,"FEE"),(5,"bad\x00name"),(6,"After"),(7," "), (8,"☕")]
        manager.executemany("INSERT INTO players VALUES(?,?)",spellings)
        manager.executemany("INSERT INTO private_slots VALUES(?,?)",[("own",3),("friend",4)])
        manager.commit()
        assert names.played_by_other(external,"own","fee")
        assert not names.played_by_other(external,"own","available")
        names.setup(manager)
        names.setup(manager)
        names.functions(external)
        def check(db):
            assert names.find(db,name_digest("before"))==0
            assert names.find(db,name_digest("fee"),friends=True)==3
            assert names.find(db,name_digest("fee"),exclude=3,friends=True)==4
            assert names.find(db,name_digest("☕"),friends=True) is None
            assert names.played_by_other(db,"own","fee") and names.played_by_other(db,"own","☕")
            assert names.blocks_rename(db,6,"fee")
            for candidate in ("after","free"):
                try:names.blocks_rename(db,8,candidate)
                except ValueError:pass
                else:raise AssertionError("Legacy malformed earlier nickname must retain native BAD_REQ")
        check(manager)
        check(external)
        with external:
            external.execute("UPDATE players SET player_name=? WHERE player_id=4",("Ｋönigin",))
            external.execute("DELETE FROM players WHERE player_id IN(5,7)")
        assert not names.played_by_other(manager,"own","fee")
        assert names.find(manager,name_digest("Ko\u0308nigin"),friends=True)==4
        assert not names.blocks_rename(manager,3,"fee")
        with manager:
            manager.execute("UPDATE players SET player_id=42,player_name='KRONE' WHERE player_id=4")
        assert names.find(manager,name_digest("königin")) is None
        assert names.find(manager,name_digest("krone"))==42
        external.execute("UPDATE players SET player_name='staged' WHERE player_id=42")
        assert names.find(external,name_digest("staged"))==42
        external.rollback()
        assert names.find(manager,name_digest("krone"))==42
        with external:
            external.execute("INSERT INTO players VALUES(100,'External')")
            external.execute("INSERT OR REPLACE INTO players VALUES(42,'LongLegacyName0123456789')")
        assert names.played_by_other(manager,"own","longlegacyname0123456789")
        assert names.find(manager,name_digest("External"))==100
        assert external.execute("PRAGMA integrity_check").fetchone()==("ok",)
        names.setup(manager)
        assert not manager.execute("SELECT 1 FROM private_player_names WHERE digest IS NULL").fetchone()
        assert names.find(manager,name_digest("External"))==100
        with manager:
            manager.execute("DELETE FROM players WHERE player_id=42")
        assert not names.played_by_other(manager,"own","longlegacyname0123456789")
        assert names.find(manager,name_digest("  ＦＥＥ "))==3
        assert _name("Ko\u0308nigin")[1]=="königin"
    with closing(sqlite3.connect(path)) as reopened:
        names.setup(reopened)
        assert names.find(reopened,name_digest("External"))==100
    print("PASS nickname index: legacy backfill, Unicode duplicates, invalid-name order, slot ownership, bare SQLite writes, UID changes, rollback, integrity and re-open.")


if __name__=="__main__":
    with TemporaryDirectory() as tmp:run(Path(tmp))
