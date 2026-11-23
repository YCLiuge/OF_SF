"""Versioned local administration API shared by Windows and Linux supervisors."""
from contextlib import closing
from dataclasses import asdict, fields
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID
from zipfile import BadZipFile

import migrate
from local_identity import LocalIdentity
from realm_maintenance import Maintenance
import realm_rules
import realm_gacha
import realm_events
from state_lock import StateBusy, exclusive_state

VERSION = "overfield.admin/v1"
MAX_REQUEST = 64 * 1024
STRING = {"type": "string", "minLength": 1}
ID = {"type": "integer", "minimum": 1}
IMAGES = {"type": "array", "maxItems": 4, "items": STRING}
RULES = {f.name: ({"type": "string", "enum": ["channel", "realm"]}
                  if f.name == "public_furniture_scope" else {"type": "integer"})
         for f in fields(realm_rules.RealmRules)}


def operation(mode, description, properties=None, required=(), minimum=0, *, read_only=False):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False,
              "required": list(required), "minProperties": minimum}
    return {"availability": mode, "description": description, "readOnly": read_only, "inputSchema": schema}


BANNER = {"type": "object", "additionalProperties": False,
          "required": ["id", "enabled", "pity", "weights"],
          "properties": {"id": ID, "enabled": {"type": "boolean"}, "pity": ID,
                         "sr_pity": {"type": "integer", "minimum": 0, "maximum": 1000},
                         "weights": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}}}}
EVENT_TIME = {"type": ["string", "null"]}
EVENT = {"type": "object", "additionalProperties": False,
         "required": ["id", "enabled", "opens_at", "closes_at"],
         "properties": {"id": ID, "enabled": {"type": "boolean"}, "opens_at": EVENT_TIME, "closes_at": EVENT_TIME}}
OPERATIONS = {
    "events.catalog": operation("any", "List supported native activity pages, game schedules and rewards.", read_only=True),
    "events.get": operation("any", "Read this realm's activity overrides.", read_only=True),
    "events.set": operation("any", "Replace the overrides for native activity pages.",
                            {"events": {"type": "array", "maxItems": 1000, "items": EVENT}}, ("events",)),
    "gacha.catalog": operation("any", "List native banners, their featured characters and reward groups.", read_only=True),
    "gacha.get": operation("any", "Return the active configuration and basis-point probabilities.", read_only=True),
    "gacha.set": operation("any", "Replace this realm's banner configuration, effective on the next request.",
                           {"pools": {"type": "array", "maxItems": 1000, "items": BANNER}}, ("pools",)),
    "realm.status": operation("any", "Read local lifecycle and persistent record counts.", read_only=True),
    "realm.stop": operation("running", "Request graceful shutdown of this server instance."),
    "rules.get": operation("any", "Read configured gameplay rules.", read_only=True),
    "rules.set": operation("stopped", "Update supplied rules atomically.", RULES, minimum=1),
    "maintenance.list": operation("any", "Read active announcements and the visible client release.", read_only=True),
    "notice.publish": operation("any", "Publish a new announcement with optional local image files.",
                                {"title": STRING, "body": STRING, "images": IMAGES}, ("title", "body")),
    "notice.edit": operation("any", "Update an active announcement. An empty image list clears its images.",
                             {"id": ID, "title": STRING, "body": STRING, "images": IMAGES}, ("id",), 2),
    "notice.withdraw": operation("any", "Withdraw the specified announcement.", {"id": ID}, ("id",)),
    "release.publish": operation("any", "Verify and publish a new client package from a local file.",
                                 dict.fromkeys(("file", "version", "sha256", "notes"), STRING),
                                 ("file", "version", "sha256", "notes")),
    "release.withdraw": operation("any", "Withdraw the currently published client package."),
    "state.backup": operation("stopped", "Back up this realm to a new archive.", {"archive": STRING}, ("archive",)),
    "state.restore": operation("stopped", "Restore a backup into an absent or empty realm directory.",
                               {"archive": STRING}, ("archive",)),
}


def arguments(commands):
    owner = commands.add_parser("admin", help="Local JSON administration API for operators and automation")
    sub = owner.add_subparsers(dest="admin_command", required=True)
    sub.add_parser("schema", help="List operations and JSON input schemas")
    sub.add_parser("status", help="Inspect this realm without starting it")
    call = sub.add_parser("call", help="Run one operation with a JSON object on stdin or in a file")
    call.add_argument("operation")
    call.add_argument("--input", type=Path, help="UTF-8 JSON file; defaults to standard input")


def count_records(filename, table):
    if not filename.is_file():
        return None
    with closing(sqlite3.connect(filename.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone():
            # Both callers supply fixed table names.
            return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    return None


def status(state):
    initialized = (state / "identity.sqlite3").is_file()
    marker = state.with_name("." + state.name + ".active")
    lock = state.with_name("." + state.name + ".server.lock")
    busy = False
    if lock.exists():
        try:
            with exclusive_state(state):
                pass
        except StateBusy:
            busy = True
    try:
        instance = UUID(marker.read_text("ascii").strip()).hex
    except (FileNotFoundError, ValueError):
        instance = None
    if busy and instance:
        pending = marker.with_name(marker.name + "." + instance + ".stop")
        lifecycle = "stopping" if pending.exists() else "running"
    elif busy:
        lifecycle = "busy"
    elif marker.exists():
        lifecycle = "stale"
    else:
        lifecycle = "stopped" if initialized else "uninitialized"
    return {"state": str(state), "lifecycle": lifecycle, "initialized": initialized,
            "accounts": count_records(state / "identity.sqlite3", "realm_accounts"),
            "characters": count_records(state / "game.sqlite", "players")}


def unique_keys(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("Duplicate JSON property: " + key)
        value[key] = child
    return value


def read_request(path, implicit_empty=False):
    if path is not None:
        with path.open("rb") as source:
            raw = source.read(MAX_REQUEST + 1)
    else:
        stdin = sys.stdin
        if stdin is None or stdin.closed or (implicit_empty and stdin.isatty()):
            raw = b""
        else:
            raw = stdin.buffer.read(MAX_REQUEST + 1)
        if not raw:
            if implicit_empty:
                return {}
            raise ValueError("Supply a JSON object on standard input or with --input")
    if len(raw) > MAX_REQUEST:
        raise ValueError("Input exceeds 64 KiB")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)


def validate(value, schema, name="input"):
    kind = schema["type"]
    if kind == ["string", "null"]:
        if value is None:
            return
        kind = "string"
    if type(value) is not {"object": dict, "string": str, "integer": int, "array": list, "boolean": bool}[kind]:
        raise ValueError(f"{name} must be a JSON {kind}")
    if kind == "object":
        known = schema.get("properties", {})
        extra = schema.get("additionalProperties", False)
        unknown = value.keys() - known.keys()
        missing = set(schema.get("required", ())) - value.keys()
        if (unknown and not extra) or missing:
            raise ValueError(f"Unknown: {sorted(unknown)}. Missing: {sorted(missing)}.")
        if len(value) < schema.get("minProperties", 0):
            raise ValueError("Supply at least one field to change")
        for key, field in value.items():
            validate(field, known.get(key, extra), key)
    elif kind == "array":
        if len(value) > schema["maxItems"]:
            raise ValueError(f"Too many items in {name}")
        for child in value:
            validate(child, schema["items"], name + "[]")
    elif kind == "string":
        if not value or ("enum" in schema and value not in schema["enum"]) or "\x00" in value:
            raise ValueError("Invalid " + name)
    elif (("minimum" in schema and value < schema["minimum"])
          or ("maximum" in schema and value > schema["maximum"])):
        raise ValueError("Invalid " + name)


def run(state, name, data, request_stop):
    if name == "events.catalog":
        return {"events": realm_events.available()}
    if name == "events.get":
        if not (state / "identity.sqlite3").is_file():
            return {"events": []}
        return realm_events.configure(state)
    if name == "events.set":
        return realm_events.configure(state, data)
    if name == "gacha.catalog":
        return {"banners": realm_gacha.available()}
    if name == "gacha.get":
        if not (state / "identity.sqlite3").is_file():
            return realm_gacha.defaults()
        return realm_gacha.configure(state)
    if name == "gacha.set":
        return realm_gacha.configure(state, data)
    if name == "realm.status":
        return status(state)
    if name == "realm.stop":
        if status(state)["lifecycle"] not in ("running", "stopping"):
            raise ValueError("The selected realm has no running server")
        request_stop(state, quiet=True)
        return {"requested": True}
    if name in ("rules.set", "rules.get"):
        if name == "rules.get" and not (state / "identity.sqlite3").is_file():
            return asdict(realm_rules.RealmRules())
        return asdict(realm_rules.configure(state, data))
    if name == "state.backup":
        return {"archive": str(migrate.backup(state, data["archive"]))}
    if name == "state.restore":
        return {"state": str(migrate.restore(data["archive"], state))}
    if name == "maintenance.list" and not (state / "identity.sqlite3").is_file():
        return {"notices": [], "release": None}
    with exclusive_state(state, purpose="profiles"):
        state.mkdir(parents=True, exist_ok=True)
        owner = Maintenance(LocalIdentity(state / "identity.sqlite3"))
        if name == "maintenance.list":
            return owner.feed()
        if name in ("notice.publish", "notice.edit"):
            paths = [Path(p) for p in data["images"]] if "images" in data else None
            if name == "notice.publish":
                identifier = owner.announce(data["title"], data["body"], images=paths or [])
            else:
                identifier = owner.edit_notice(data["id"], data.get("title"), data.get("body"), images=paths)
            return {"id": identifier}
        if name == "notice.withdraw":
            owner.withdraw(data["id"])
            return {"id": data["id"]}
        if name == "release.publish":
            return owner.publish(Path(data["file"]), data["version"], data["sha256"], data["notes"])
        if name == "release.withdraw":
            owner.withdraw_release()
            return {"withdrawn": True}
    raise AssertionError("Unimplemented management operation")


def command(state, args, request_stop):
    response = {"apiVersion": VERSION}
    try:
        if args.admin_command == "schema":
            result = {"operations": OPERATIONS, "maxInputBytes": MAX_REQUEST}
        elif args.admin_command == "status":
            result = status(state)
        else:
            spec = OPERATIONS.get(args.operation)
            if spec is None:
                raise KeyError(args.operation)
            implicit_empty = spec["readOnly"] and not spec["inputSchema"]["properties"]
            data = read_request(args.input, implicit_empty)
            validate(data, spec["inputSchema"])
            result = run(state, args.operation, data, request_stop)
        response.update(ok=True, result=result)
        exit_code = 0
    except (KeyError, ValueError, StateBusy, OSError, sqlite3.Error, BadZipFile, RecursionError) as error:
        if isinstance(error, StateBusy):
            code = "state_busy"
        elif isinstance(error, KeyError):
            code = "unknown_operation"
        elif isinstance(error, FileNotFoundError):
            code = "not_found"
        elif isinstance(error, FileExistsError):
            code = "already_exists"
        elif isinstance(error, (OSError, sqlite3.Error)):
            code = "storage_error"
        else:
            code = "invalid_request"
        response.update(ok=False, error={"code": code, "message": str(error)})
        exit_code = 1
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    return exit_code

