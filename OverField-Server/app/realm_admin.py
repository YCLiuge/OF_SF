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
from state_lock import StateBusy, exclusive_state

VERSION = "overfield.admin/v1"
MAX_REQUEST = 64 * 1024
STRING = {"type": "string", "minLength": 1}
ID = {"type": "integer", "minimum": 1}
IMAGES = {"type": "array", "maxItems": 4, "items": STRING}
RULES = {f.name: ({"type": "string", "enum": ["channel", "realm"]}
                  if f.name == "public_furniture_scope" else {"type": "integer"})
         for f in fields(realm_rules.RealmRules)}


def operation(mode, description, properties=None, required=(), minimum=0):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False,
              "required": list(required), "minProperties": minimum}
    return {"availability": mode, "description": description, "inputSchema": schema}


OPERATIONS = {
    "realm.status": operation("any", "Read local lifecycle and persistent record counts."),
    "realm.stop": operation("running", "Request graceful shutdown of this server instance."),
    "rules.get": operation("any", "Read configured gameplay rules."),
    "rules.set": operation("stopped", "Update supplied rules atomically.", RULES, minimum=1),
    "maintenance.list": operation("any", "Read active announcements and the visible client release."),
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


def read_request(path):
    def read(stream):
        raw = stream.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("Input exceeds 64 KiB")
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
    if path:
        with path.open("rb") as source:
            return read(source)
    return read(sys.stdin.buffer)


def validate(value, schema, name="input"):
    kind = schema["type"]
    if type(value) is not {"object": dict, "string": str, "integer": int, "array": list}[kind]:
        raise ValueError(f"{name} must be a JSON {kind}")
    if kind == "object":
        known = schema["properties"]
        unknown = value.keys() - known.keys()
        missing = set(schema["required"]) - value.keys()
        if unknown or missing:
            raise ValueError(f"Unknown: {sorted(unknown)}. Missing: {sorted(missing)}.")
        if len(value) < schema["minProperties"]:
            raise ValueError("Supply at least one field to change")
        for key, field in value.items():
            validate(field, known[key], key)
    elif kind == "array":
        if len(value) > schema["maxItems"]:
            raise ValueError(f"Too many items in {name}")
        for child in value:
            validate(child, schema["items"], name + "[]")
    elif kind == "string":
        if not value or ("enum" in schema and value not in schema["enum"]) or "\x00" in value:
            raise ValueError("Invalid " + name)
    elif "minimum" in schema and value < schema["minimum"]:
        raise ValueError("Invalid " + name)


def run(state, name, data, request_stop):
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
            data = read_request(args.input)
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

