"""Versioned local administration API shared by Windows and Linux supervisors."""
from contextlib import closing
from dataclasses import asdict, fields
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID
from zipfile import BadZipFile

from config_revision import ConfigChanged
import migrate
from local_identity import LocalIdentity
from realm_maintenance import Maintenance, admin_feed
import realm_rules
import realm_gacha
import realm_events
import realm_plaza
from state_lock import StateBusy, exclusive_state

VERSION = "overfield.admin/v1"
MAX_REQUEST = 64 * 1024
STRING = {"type": "string", "minLength": 1}
ID = {"type": "integer", "minimum": 1}
IMAGES = {"type": "array", "maxItems": 4, "items": STRING}
RULES = {f.name: ({"type": "string", "enum": ["channel", "realm"]}
                  if f.name == "public_furniture_scope" else {"type": "integer"})
         for f in fields(realm_rules.RealmRules)}


# Range/identity changes retain their exclusive stopped-server workflow.
NEXT_START_RULES = {key: rule for key, rule in RULES.items()
                    if key not in {"public_min", "public_max", "uid_start", "world_min", "world_max", "game_connections"}}


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
         "properties": {"id": ID, "enabled": {"type": "boolean"}, "opens_at": EVENT_TIME, "closes_at": EVENT_TIME,
                        "staged_gifts": {"type":"boolean"}}}
CODE_REWARD = {"type":"object","properties":{"item":ID,"quantity":{"type":"integer","minimum":1,"maximum":1000000}},
               "required":["item","quantity"],"additionalProperties":False}
OPERATIONS = {
    "rewards.catalog": operation("any", "按名称、编号或类别查询可发放的奖励。",
        {"channel":{"type":"string","enum":["mail","codes"]}, "group":STRING, "query":STRING,
         "offset":{"type":"integer","minimum":0,"maximum":1000000}}, ("channel",), read_only=True),
    "mail.send": operation("any","发送原生游戏邮件；相同批次号可安全重试。全服范围可选择仅现有账号，或在有效期内包含新玩家。",
        {"request_id":STRING, "scope":{"type":"string","enum":["account","existing","ongoing"]},
         "username":STRING, "sender":STRING, "title":STRING, "body":STRING,
         "days":{"type":"integer","minimum":1,"maximum":365},
         "rewards":{"type":"array","maxItems":16,"items":CODE_REWARD}},
        ("request_id","scope","title","body")),
    "mail.list": operation("any","按时间倒序查看服主投递记录、范围及发送时的接收人数。",
        {"before":ID,"limit":{"type":"integer","minimum":1,"maximum":100},"include_names":{"type":"boolean"}},read_only=True),
    "accounts.find": operation("any","精确查找本服登录账号、游戏名或公开 UID。",
        {"field":{"type":"string","enum":["username","nickname","uid"]},"value":STRING},("field","value"),read_only=True),
    "accounts.reset": operation("any","为选中的本服账号设置新密码。旧登录票和重连凭据立即失效。",
        {"username":STRING,"account_ref":STRING,"password_version":{"type":"integer","minimum":0},
         "new_password":{"type":"string","format":"password","minLength":8,"maxLength":128,"writeOnly":True}},
        ("username","account_ref","password_version","new_password")),
    "reports.list": operation("any","查询玩家主动提交的举报，可分页查看待处理或已处理的记录。",
                              {"before":ID,"limit":{"type":"integer","minimum":1,"maximum":100},
                               "resolved":{"type":"boolean"}},read_only=True),
    "reports.resolve": operation("any","将一条举报标记为已处理。",{"id":ID},("id",)),
    "codes.list": operation("any", "列出本服发行的兑换码、数量和有效期。", {"include_names":{"type":"boolean"}}, read_only=True),
    "codes.issue": operation("any", "发行区服奖励码。kind 选 redemption（普通）或 inherit（继承奖励）。quota 为总名额，0 代表不限，每个角色可领一次。",
                             {"code":STRING,"kind":{"type":"string","enum":["redemption","inherit"]},"quota":{"type":"integer","minimum":0,"maximum":10000000},
                              "expires_at":EVENT_TIME,"rewards":{"type":"array","maxItems":16,"items":CODE_REWARD}},
                             ("code","quota","rewards")),
    "codes.withdraw": operation("any","停止此码后续兑换。",{"code":STRING},("code",)),
    "blessings.catalog": operation("any", "查看国服祈愿树名称、期数、兑换节点、货币和原始排期。",read_only=True),
    "blessings.get": operation("any", "读取区服各期祈愿树的排期覆盖。",read_only=True),
    "blessings.set": operation("any", "设置各期祈愿树排期。空数组恢复国服原始日历。",
        {"trees":{"type":"array","maxItems":63,"items":{"type":"object","additionalProperties":False,
         "required":["id","enabled","opens_at","closes_at"],
         "properties":{"id":ID,"enabled":{"type":"boolean"},"opens_at":EVENT_TIME,"closes_at":EVENT_TIME}}}},("trees",)),
    "plaza.catalog": operation("any", "List native plaza stages, seasonal arcades and activity entrances.", read_only=True),
    "plaza.get": operation("any", "Read the configured plaza schedule.", read_only=True),
    "plaza.set": operation("any", "Schedule the plaza stage and its entrances; an empty object restores retail dates.",
                           {"theme": ID, "arcade": {"type": "integer", "minimum": 0},
                            "activities": {"type": "array", "maxItems": 64, "items": ID},
                            "opens_at": STRING, "closes_at": STRING}),
    "events.catalog": operation("any", "List supported native activity pages, game schedules and rewards.", read_only=True),
    "events.get": operation("any", "Read this realm's activity overrides.", read_only=True),
    "events.set": operation("any", "Replace the overrides for native activity pages.",
                            {"events": {"type": "array", "maxItems": 1000, "items": EVENT}}, ("events",)),
    "gacha.catalog": operation("any", "List native banners, their featured characters and reward groups.", read_only=True),
    "gacha.enable": operation("any", "Enable one featured banner and preserve every other setting.",
                              {"id": ID}, ("id",)),
    "gacha.get": operation("any", "Return the active configuration and basis-point probabilities.", read_only=True),
    "gacha.set": operation("any", "Replace this realm's banner configuration, effective on the next request.",
                           {"pools": {"type": "array", "maxItems": 1000, "items": BANNER}}, ("pools",)),
    "capacity.get": operation("any", "读取游戏连接上限和当前连接数。", read_only=True),
    "capacity.set": operation("any", "调整游戏连接上限，在线玩家可继续游戏。",
                              {"limit": {"type": "integer", "minimum": 1, "maximum": 512}}, ("limit",)),
    "realm.status": operation("any", "Read local lifecycle and persistent record counts.", read_only=True),
    "realm.stop": operation("running", "Request graceful shutdown of this server instance."),
    "rules.schedule": operation("any", "预设下次启动时生效的玩法参数。", NEXT_START_RULES, minimum=1),
    "rules.get": operation("any", "Read configured gameplay rules.", read_only=True),
    "rules.set": operation("stopped", "Update supplied rules atomically.", RULES, minimum=1),
    "photos.list": operation("any", "List published player photos, newest first.",
                             {"before": {"type":"integer","minimum":1,"maximum":2**63-1},
                              "limit": {"type":"integer","minimum":1,"maximum":100}},read_only=True),
    "photos.withdraw": operation("any", "Withdraw one published player photo by its share code.",
                                 {"code":{"type":"integer","minimum":1,"maximum":2**63-1}}, ("code",)),
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


def count_records(filename, table, *, allow_busy=False):
    if not filename.is_file():
        return None
    try:
        timeout = 0 if allow_busy else 2
        with closing(sqlite3.connect(filename.as_uri() + "?mode=ro", uri=True, timeout=timeout)) as db:
            if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone():
                # Both callers supply fixed table names.
                return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    except sqlite3.OperationalError as error:
        primary_code = getattr(error, "sqlite_errorcode", 0) & 0xff
        if not allow_busy or primary_code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise
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
            "accounts": count_records(state / "identity.sqlite3", "realm_accounts", allow_busy=lifecycle=="busy"),
            "characters": count_records(state / "game.sqlite", "players", allow_busy=lifecycle=="busy")}


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
    return decode_request(raw)


def decode_request(raw):
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


def run(state, name, data, request_stop, expected=None, live_capacity=None):
    if name == "rewards.catalog":
        from realm_reward_catalog import search
        return search(data)
    if name in ("mail.send","mail.list"):
        import realm_mail
        if name == "mail.send":
            return realm_mail.send(state,data)
        result = realm_mail.recent(state,data)
        if data.get("include_names"):
            from realm_reward_catalog import label_history
            return label_history(result, (reward["item"] for batch in result["batches"] for reward in batch["rewards"]))
        return result
    if name in ("accounts.find","accounts.reset"):
        import realm_account_admin
        if name=="accounts.find":return realm_account_admin.find(state,data["field"],data["value"])
        return realm_account_admin.reset(state,data)
    if name in ("capacity.get", "capacity.set"):
        if live_capacity is not None:
            return live_capacity(data.get("limit"))
        changes = {"game_connections": data["limit"]} if name == "capacity.set" else {}
        rules = realm_rules.configure(state, changes)
        return {"limit": rules.game_connections, "used": None}
    if name in ("reports.list", "reports.resolve"):
        from realm_reports import manage
        with exclusive_state(state,purpose="profiles") as root:
            return manage(root,name,data)
    if name in ("codes.list", "codes.issue", "codes.withdraw"):
        from realm_codes import manage
        result = manage(state,name,data)
        if name == "codes.list" and data.get("include_names"):
            from realm_reward_catalog import label_history
            return label_history(result, (key for code in result["codes"] for key in code["rewards"]))
        return result
    if name in ("photos.list", "photos.withdraw"):
        from photo_media import moderate
        if not (state/"game.sqlite").is_file() and name=="photos.list":
            return {"photos":[]}
        with exclusive_state(state,purpose="profiles") as root:
            return moderate(root,name,data)
    if name in ("blessings.catalog","blessings.get","blessings.set"):
        import realm_blessings
        return realm_blessings.manage(state,name,data, expected=expected)
    if name == "plaza.catalog":
        return realm_plaza.catalog()
    if name == "plaza.get":
        return realm_events.configure(state, plaza=True) if (state / "identity.sqlite3").is_file() else {}
    if name == "plaza.set":
        return realm_events.configure(state, data, plaza=True, expected=expected)
    if name == "events.catalog":
        return {"events": realm_events.available()}
    if name == "events.get":
        if not (state / "identity.sqlite3").is_file():
            return {"events": []}
        return realm_events.configure(state)
    if name == "events.set":
        return realm_events.configure(state, data, expected=expected)
    if name == "gacha.catalog":
        banners = realm_gacha.available()
        presets = {r["id"]: r for r in realm_gacha.defaults()["pools"]}
        for row in banners:
            if row["featured"] and row["id"] not in presets:
                presets[row["id"]] = realm_gacha.featured_pool(row["id"])
        return {"banners": banners, "presets": presets}
    if name == "gacha.get":
        if not (state / "identity.sqlite3").is_file():
            return realm_gacha.defaults()
        return realm_gacha.configure(state)
    if name == "gacha.enable":
        return realm_gacha.configure(state, enable_featured=data["id"])
    if name == "gacha.set":
        return realm_gacha.configure(state, data, expected=expected)
    if name == "realm.status":
        return status(state)
    if name == "realm.stop":
        if status(state)["lifecycle"] not in ("running", "stopping"):
            raise ValueError("The selected realm has no running server")
        request_stop(state, quiet=True)
        return {"requested": True}
    if name == "rules.schedule":
        return asdict(realm_rules.configure(state, data, next_start=True, expected=expected))
    if name in ("rules.set", "rules.get"):
        if name == "rules.get" and not (state / "identity.sqlite3").is_file():
            return asdict(realm_rules.RealmRules())
        return asdict(realm_rules.configure(state, data))
    if name == "state.backup":
        return {"archive": str(migrate.backup(state, data["archive"]))}
    if name == "state.restore":
        return {"state": str(migrate.restore(data["archive"], state))}
    if name == "maintenance.list":
        return admin_feed(state)
    with exclusive_state(state, purpose="profiles"):
        state.mkdir(parents=True, exist_ok=True)
        owner = Maintenance(LocalIdentity(state / "identity.sqlite3"))
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


def operation_spec(name):
    if name not in OPERATIONS:
        raise KeyError(name)
    return OPERATIONS[name]


def execute(state, name, data, request_stop, *, authorize=None, expected=None, live_capacity=None):
    spec = operation_spec(name)
    validate(data, spec["inputSchema"])
    if authorize:
        authorize(name, spec, data)
    return run(state, name, data, request_stop, expected, live_capacity)


def schema():
    return {"operations": OPERATIONS, "maxInputBytes": MAX_REQUEST}


def response_for(action):
    try:
        return {"apiVersion": VERSION, "ok": True, "result": action()}
    except (KeyError, ValueError, StateBusy, OSError, sqlite3.Error, BadZipFile, RecursionError) as error:
        if isinstance(error, ConfigChanged):
            code = "config_changed"
        elif isinstance(error, StateBusy):
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
        return {"apiVersion": VERSION, "ok": False, "error": {"code": code, "message": str(error)}}


def command(state, args, request_stop):
    def action():
        if args.admin_command == "schema":
            return schema()
        if args.admin_command == "status":
            return status(state)
        spec = operation_spec(args.operation)
        implicit_empty = spec["readOnly"] and not spec["inputSchema"]["properties"]
        return execute(state, args.operation, read_request(args.input, implicit_empty), request_stop)

    response = response_for(action)
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    return 0 if response["ok"] else 1
