"""Bounded, optional stage markers for a single private realm."""
from contextlib import contextmanager
import json
import os
import threading
import time

_ROUTES = {
    "/dispatch/get_login_url_list": "login_urls",
    "/dispatch/client_hot_update": "hot_update",
    "/dispatch/region_info": "region_info",
    "/dispatch/get_notice_list": "notices",
    "/dispatch/get_notice_url_list": "notice_urls",
    "/local/v1/region": "private_region",
    "/local/v1/profiles": "private_profiles",
    "/local/v1/sessions": "private_sessions",
    "/healthz": "health",
}
MAX_BYTES = 8192


class StageDiagnostics:
    def __init__(self, state):
        self.path = state / "stage-events.jsonl"
        self.previous = state / "stage-events.previous.jsonl"
        self.failures = state / "stage-failures.jsonl"
        self.old_failures = state / "stage-failures.previous.jsonl"
        self.operations = state / "stage-operations.jsonl"
        self.old_operations = state / "stage-operations.previous.jsonl"
        self.auth = state / "stage-login.jsonl"
        self.old_auth = state / "stage-login.previous.jsonl"
        self.lock = threading.Lock()
        self.enabled = os.environ.get("OF_STAGE_DIAGNOSTICS") == "1"
        if self.enabled:
            from network.msg_id import MsgId
            from proto import net_pb2 as pb
            self.variant_requests = {}
            for name, request_id in vars(MsgId).items():
                if not name.endswith("Req") or name in ("VerifyLoginTokenReq", "PlayerLoginReq"):
                    continue
                message = getattr(pb, name, None)
                response_id = getattr(MsgId, name[:-3] + "Rsp", None)
                if not message or response_id is None:
                    continue
                enums = [f for f in message.DESCRIPTOR.fields if f.enum_type and not f.is_repeated]
                if enums or name == "TutorialReq":
                    self.variant_requests[request_id] = (response_id, message, enums)
            self.ids = frozenset(v for v in vars(MsgId).values() if type(v) is int and v > 0)
            try:
                state.mkdir(parents=True, exist_ok=True)
                if self.path.is_symlink() or self.previous.is_symlink():
                    raise OSError("Unsupported diagnostics path")
                if self.path.exists():
                    self.path.replace(self.previous)
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except OSError:
                self.enabled = False

    def http(self, target):
        if not self.enabled:
            return
        route = target.partition("?")[0]
        label = _ROUTES.get(route)
        if label is None and route.startswith("/Resources/"):
            label = "resources"
        if label is not None:
            self._write("http", label)

    def message(self, msg_id):
        if self.enabled:
            if type(msg_id) is int and msg_id in self.ids:
                self._write("msg_id", msg_id)
            else:
                self._write("tcp", "unknown_msg")

    def connection(self, opened):
        if self.enabled:
            self._write("tcp", "open" if opened else "close")

    def unsupported(self, msg_id):
        if self.enabled and msg_id in self.ids:
            self._write("unsupported_msg_id", msg_id)

    def closed_because(self, reason):
        if self.enabled and reason in {
            "server_stop", "protocol", "verify_rejected", "unsupported_message",
            "handler_decode", "handler_data", "handler_storage", "handler_error",
            "timeout", "eof", "transport", "server_error",
        }:
            self._write("close_reason", reason)

    def verified(self, accepted):
        if self.enabled:
            self._write("verify", "ok" if accepted else "rejected")

    @contextmanager
    def login_step(self, stage):
        # Separate retention keeps movement and heartbeats from evicting launch diagnostics.
        if not self.enabled or stage not in ("channel", "local_handoff", "realm_connect"):
            yield
            return
        start = time.monotonic()
        result = "ok"
        try:
            yield
        except Exception as error:
            cause = error.__cause__ or error
            if isinstance(cause, TimeoutError):
                result = "timeout"
            elif isinstance(cause, (OSError, EOFError)):
                result = "transport"
            elif isinstance(error, ValueError):
                result = "not_confirmed"
            else:
                result = "error"
            raise
        finally:
            elapsed = max(0, min(600000, int((time.monotonic() - start) * 1000)))
            self._write("login", {"time": int(time.time()), "stage": stage,
                        "result": result, "elapsed_ms": elapsed}, self.auth, self.old_auth)

    def gameplay_reward(self, template, amount):
        if self.enabled:
            # Public native gameplay templates and small unit counts only.
            value=template if type(template) is int and template in (1001,1002,1003) else "other"
            units=amount if type(amount) is int and amount in (0,1,2,3) else "other"
            self._write("play_reward",[value,units])

    def failure(self, msg_id, status):
        if self.enabled and msg_id in self.ids and type(status) is int and 1 < status < 65536:
            self._write("failure", [msg_id, status], self.failures, self.old_failures)

    def failed_operation(self, request, reply_id, status):
        # Log only statically declared operation modes. Arbitrary request parameters stay out of diagnostics.
        if not self.enabled or request is None:
            return
        request_id, data = request
        spec = self.variant_requests.get(request_id)
        if spec is None or spec[0] != reply_id:
            return
        msg = spec[1].FromString(data)
        values = {}
        for field in spec[2]:
            code = getattr(msg, field.name)
            values[field.name] = code if code in field.enum_type.values_by_number else "other"
        if msg.DESCRIPTOR.name == "TutorialReq":
            from handlers.Tutorial import EVENTS, STAR_EVENTS
            code = msg.tutorial_id
            values["tutorial_id"] = code if code in EVENTS or code in STAR_EVENTS else "other"
        self._write("operation", {"time":int(time.time()),"request":request_id,"reply":reply_id,
                                 "status":status,"modes":values}, self.operations, self.old_operations)

    def _write(self, key, value, path=None, previous=None):
        path = path or self.path
        previous = previous or self.previous
        line = (json.dumps({key: value}, separators=(",", ":")) + "\n").encode("ascii")
        with self.lock:
            if not self.enabled:
                return
            try:
                if path.is_symlink() or previous.is_symlink():
                    raise OSError("Unsupported diagnostics path")
                if path.exists() and path.stat().st_size + len(line) > MAX_BYTES:
                    path.replace(previous)
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(path, flags, 0o600), "ab") as output:
                    output.write(line)
            except OSError:
                self.enabled = False
