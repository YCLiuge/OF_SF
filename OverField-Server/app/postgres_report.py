"""Refresh or verify the PostgreSQL reporting schema from a stopped-realm backup."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import migrate
from proto import net_pb2 as pb
from google.protobuf.message import DecodeError


# Fixed, schema-qualified destination. No account names, secrets, tickets, or raw protobufs.
SCHEMA = "of_sf_report"
COLUMNS = {
    "players": ("uid", "nickname", "level", "world_level", "scene", "channel", "has_profile", "has_account"),
    "inventory": ("uid", "storage", "entry_id", "item_id", "bag_tag", "quantity"),
    "snapshot": ("id", "version", "identity_sha256", "game_sha256", "profiles", "accounts", "players", "inventory"),
}
KEYS = {"players": ("uid",), "inventory": ("uid", "storage", "entry_id"), "snapshot": ("id",)}
DDL = """
CREATE SCHEMA IF NOT EXISTS of_sf_report;
CREATE TABLE IF NOT EXISTS of_sf_report.players (
    uid bigint PRIMARY KEY CHECK (uid BETWEEN 1 AND 4294967294),
    nickname text, level bigint, world_level bigint, scene bigint, channel bigint,
    has_profile boolean NOT NULL, has_account boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS of_sf_report.inventory (
    uid bigint NOT NULL REFERENCES of_sf_report.players(uid), storage smallint NOT NULL CHECK(storage IN (0,1)),
    entry_id bigint NOT NULL, item_id bigint NOT NULL, bag_tag bigint NOT NULL, quantity bigint,
    PRIMARY KEY(uid,storage,entry_id)
);
CREATE TABLE IF NOT EXISTS of_sf_report.snapshot (
    id smallint PRIMARY KEY CHECK(id=1), version integer NOT NULL, identity_sha256 text NOT NULL,
    game_sha256 text NOT NULL, profiles bigint NOT NULL, accounts bigint NOT NULL,
    players bigint NOT NULL, inventory bigint NOT NULL
);
CREATE INDEX IF NOT EXISTS of_sf_inventory_item ON of_sf_report.inventory(item_id,uid);
CREATE INDEX IF NOT EXISTS of_sf_player_scene ON of_sf_report.players(scene,channel,uid);
"""


def databases(archive, folder):
    with ZipFile(archive) as source:
        _, files = migrate.read_manifest(source)
        for filename in sorted(migrate.DATABASES):
            destination = folder / filename
            with source.open("data/" + filename) as original, destination.open("xb") as output:
                actual = migrate.copy_hash(original, output)
            if actual != files[filename]:
                raise ValueError(f"Backup digest mismatch for {filename}")
            migrate.check_db(destination)
    return files


def readonly(path):
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def has_table(db, schema, table):
    return db.execute(f"SELECT 1 FROM {schema}.sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone() is not None


def source_rows(db, files):
    # Optional tables were introduced after the original local-only profiles.
    alias = has_table(db, "main", "realm_public_uids")
    slots = has_table(db, "main", "private_slots")
    locations = has_table(db, "main", "private_locations")
    accounts = has_table(db, "identity", "realm_accounts")
    channel = locations and any(r[1] == "channel" for r in db.execute("PRAGMA table_info(private_locations)"))
    joins = []
    if alias:
        joins.append("LEFT JOIN realm_public_uids u ON u.player_id=p.player_id")
    if slots:
        joins.append("LEFT JOIN private_slots s ON s.player_id=p.player_id")
    if locations:
        joins.append("LEFT JOIN private_locations l ON l.player_id=p.player_id")
    uid = "coalesce(u.uid,p.player_id)" if alias else "p.player_id"
    profile = "EXISTS(SELECT 1 FROM identity.profiles a WHERE a.slot=s.slot)" if slots else "0"
    account = "EXISTS(SELECT 1 FROM identity.realm_accounts a WHERE a.slot=s.slot)" if accounts and slots else "0"
    scene = "l.scene" if locations else "NULL"
    chan = "l.channel" if channel else "NULL"
    sql = f"SELECT {uid},p.player_name,p.level,p.world_level,{scene},{chan},{profile},{account} FROM players p "
    players = inventory = 0
    for row in db.execute(sql + " ".join(joins)):
        players += 1
        yield "players", row
    for storage, table, key in ((0,"items","item_id"), (1,"items_s","instance_id")):
        owner = "coalesce(u.uid,i.player_id)" if alias else "i.player_id"
        join = "LEFT JOIN realm_public_uids u ON u.player_id=i.player_id" if alias else ""
        for player, entry_id, blob in db.execute(f"SELECT {owner},i.{key},i.item_detail_blob FROM {table} i {join}"):
            try:
                detail = pb.ItemDetail.FromString(blob)
            except (DecodeError, TypeError) as error:
                raise ValueError("An inventory row contains an invalid item record") from error
            if not detail.HasField("main_item") or not detail.main_item.item_id:
                raise ValueError("An inventory row has no valid item template")
            item = detail.main_item
            quantity = item.base_item.num if item.HasField("base_item") else None
            inventory += 1
            yield "inventory", (player, storage, entry_id, item.item_id, item.item_tag, quantity)
    profiles = db.execute("SELECT count(*) FROM identity.profiles").fetchone()[0]
    registered = db.execute("SELECT count(*) FROM identity.realm_accounts").fetchone()[0] if accounts else 0
    yield "snapshot", (1, 1, files["identity.sqlite3"]["sha256"], files["game.sqlite"]["sha256"],
                       profiles, registered, players, inventory)


def copy_field(value):
    if value is None:
        return r"\N"
    if not isinstance(value,str):
        return str(value)
    if "\0" in value:
        raise ValueError("A projected string contains NUL, which PostgreSQL text cannot represent")
    return value.replace("\\","\\\\").replace("\t",r"\t").replace("\n",r"\n").replace("\r",r"\r")


def difference(table):
    keys = KEYS[table]
    columns = COLUMNS[table]
    expression = " OR ".join(f"t.{key} IS DISTINCT FROM n.{key}" for key in columns if key not in keys)
    key = keys[0]
    return (f"EXISTS(SELECT 1 FROM {SCHEMA}.{table} t FULL JOIN pg_temp.new_{table} n "
            f"USING({','.join(keys)}) WHERE t.{key} IS NULL OR n.{key} IS NULL OR {expression})")


def upsert(table):
    keys = KEYS[table]
    fields = COLUMNS[table]
    changed = [field for field in fields if field not in keys]
    comparison = " OR ".join(f"t.{c} IS DISTINCT FROM n.{c}" for c in changed)
    return (f"INSERT INTO {SCHEMA}.{table}({','.join(fields)}) "
            f"SELECT {','.join('n.'+c for c in fields)} FROM pg_temp.new_{table} n "
            f"LEFT JOIN {SCHEMA}.{table} t USING({','.join(keys)}) "
            f"WHERE t.{keys[0]} IS NULL OR {comparison} "
            f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET " +
            ",".join(f"{c}=EXCLUDED.{c}" for c in changed) + ";")


def transfer(stream, connection, files, mode):
    def emit(sql):
        stream.write((sql+"\n").encode("utf8"))

    emit(r"\set VERBOSITY terse")
    emit("BEGIN;\nSET LOCAL client_min_messages TO warning;\nSET LOCAL search_path TO pg_catalog,pg_temp;\n"
         "SET LOCAL lock_timeout TO '30s';\nSELECT pg_advisory_xact_lock(1129836111,7319001);")
    if mode=="refresh":
        emit(DDL)
    for table in COLUMNS:
        emit(f"CREATE TEMP TABLE new_{table} (LIKE {SCHEMA}.{table} INCLUDING CONSTRAINTS) ON COMMIT DROP;")
    active = None
    summary = None
    for table,row in source_rows(connection,files):
        if table!=active:
            if active is not None:
                emit(r"\.")
            emit(f"COPY pg_temp.new_{table}({','.join(COLUMNS[table])}) FROM STDIN;")
            active=table
        emit("\t".join(map(copy_field,row)))
        if table=="snapshot":
            summary=row
    emit(r"\.")
    for table,keys in KEYS.items():
        emit(f"CREATE UNIQUE INDEX ON pg_temp.new_{table}({','.join(keys)});")
        emit(f"ANALYZE pg_temp.new_{table};")
    if mode=="verify":
        changed=" OR ".join(difference(table) for table in ("snapshot","players","inventory"))
        emit(f"DO $verify$ BEGIN IF {changed} THEN "
             "RAISE EXCEPTION 'PostgreSQL reporting rows differ from the source backup'; END IF; END $verify$;")
    else:
        # Remove children first. An unchanged row is neither locked for update nor rewritten.
        for table in ("inventory","players"):
            match=" AND ".join(f"t.{k}=n.{k}" for k in KEYS[table])
            emit(f"DELETE FROM {SCHEMA}.{table} t WHERE NOT EXISTS "
                 f"(SELECT 1 FROM pg_temp.new_{table} n WHERE {match});")
        for table in COLUMNS:
            emit(upsert(table))
        emit("ANALYZE of_sf_report.players;\nANALYZE of_sf_report.inventory;")
    emit("COMMIT;")
    return dict(zip(COLUMNS["snapshot"],summary))


def project(mode, archive, psql="psql"):
    if mode not in ("refresh","verify"):
        raise ValueError("Unknown PostgreSQL projection mode")
    with TemporaryDirectory(prefix="of-pg-report-") as tmp:
        folder=Path(tmp)
        try:
            files=databases(archive,folder)
        except BadZipFile as error:
            raise ValueError("Invalid or damaged OF_SF backup ZIP") from error
        with closing(sqlite3.connect(readonly(folder/"game.sqlite"),uri=True)) as db:
            db.execute("ATTACH DATABASE ? AS identity",(readonly(folder/"identity.sqlite3"),))
            db.execute("PRAGMA query_only=ON")
            args=[str(psql),"-X","-q","-A","-t","--no-password","--set","ON_ERROR_STOP=on"]
            # libpq reads its normal service, TLS and password-file configuration.
            env=dict(os.environ,PGCLIENTENCODING="UTF8")
            proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,env=env)
            try:
                result=transfer(proc.stdin,db,files,mode)
                proc.stdin.close()
            except BaseException:
                proc.kill()
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
                proc.wait()
                raise
            if proc.wait():
                raise RuntimeError("PostgreSQL command failed; use verify to check the committed projection")
    return {"operation":mode,"profiles":result["profiles"],"accounts":result["accounts"],
            "players":result["players"],"inventory":result["inventory"]}
