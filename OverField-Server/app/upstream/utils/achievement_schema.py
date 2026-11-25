"""Upgrade the retail achievement key relationship without changing saved achievements."""
import re

# The legacy schema incorrectly made both columns in the composite key reference players.
_BAD_REFERENCE = re.compile(
    r",\s*FOREIGN\s+KEY\s*\(\s*achieve_id\s*\)\s*REFERENCES\s+players\s*"
    r"\(\s*player_id\s*\)\s*ON\s+DELETE\s+CASCADE\b", re.IGNORECASE
)


def repair_achievement_schema(conn):
    keys = conn.execute("PRAGMA main.foreign_key_list(achieve)").fetchall()
    if not any(row[2:5] == ("players", "achieve_id", "player_id") for row in keys):
        return
    # This runs at startup before gameplay writers are admitted or foreign keys are enabled.
    conn.execute("SAVEPOINT repair_achievement_schema")
    try:
        original = conn.execute("SELECT sql FROM main.sqlite_schema WHERE type='table' AND name='achieve'").fetchone()[0]
        corrected, count = _BAD_REFERENCE.subn("", original)
        if count != 1:
            raise ValueError("Unrecognized legacy achievement constraint; original table preserved")
        objects = conn.execute(
            "SELECT sql FROM main.sqlite_schema WHERE tbl_name='achieve' AND type IN ('index','trigger') "
            "AND sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
        columns = [row[1] for row in conn.execute("PRAGMA main.table_xinfo(achieve)") if row[6] == 0]
        fields = "rowid," + ",".join('"' + name.replace('"','""') + '"' for name in columns)
        conn.execute("CREATE TEMP TABLE of_achievement_copy AS SELECT " + fields + " FROM main.achieve")
        conn.execute("DROP TABLE main.achieve")
        conn.execute(corrected)
        conn.execute("INSERT INTO main.achieve(" + fields + ") SELECT " + fields + " FROM temp.of_achievement_copy")
        for (sql,) in objects:
            conn.execute(sql)
        conn.execute("DROP TABLE temp.of_achievement_copy")
    except BaseException:
        conn.execute("ROLLBACK TO repair_achievement_schema")
        raise
    finally:
        conn.execute("RELEASE repair_achievement_schema")
