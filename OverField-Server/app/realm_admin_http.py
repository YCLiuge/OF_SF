"""Opt-in loopback transport for the existing operator API."""
from contextlib import contextmanager
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import socket
import threading
from urllib.parse import parse_qsl

from config_revision import revision
import realm_admin as admin

PREFIX = "/admin/v1"
OPERATIONS = PREFIX + "/operations/"
READ_CONFIG = {name + ".get" for name in ("events", "blessings", "plaza", "gacha", "rules")}
WRITE_CONFIG = {"events.set", "blessings.set", "plaza.set", "gacha.set", "rules.schedule"}
STATUS = {"config_changed": 412, "invalid_request": 400, "unknown_operation": 404, "not_found": 404,
          "already_exists": 409, "state_busy": 409, "storage_error": 500}


def configuration(game_ports, environ=os.environ):
    raw = environ.get("OF_ADMIN_PORT")
    if raw is None:
        return None
    if not re.fullmatch(r"[0-9]{1,5}", raw) or not 1 <= int(raw) <= 65535 or int(raw) in game_ports:
        raise ValueError("OF_ADMIN_PORT must be in 1..65535 and distinct from the profile and game ports.")
    token = environ.get("OF_ADMIN_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", token):
        raise ValueError("Set OF_ADMIN_TOKEN to a separate random 32..256 character URL-safe token.")
    return int(raw), ("Bearer " + token).encode("ascii")


def authorize_operation(name, spec, data):
    if spec["availability"] == "stopped":
        raise admin.StateBusy("Stop the server and run this operation using the local CLI.")
    if name in ("notice.publish", "notice.edit"):
        paths = data.get("images", [])
    elif name == "release.publish":
        paths = [data["file"]]
    else:
        return
    for path in paths:
        if path.replace("\\", "/").startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path):
            raise ValueError("Supply a local file path for publication.")


def openapi():
    obj = {"type": "object"}
    result = {"type": "object", "required": ["apiVersion", "ok", "result"],
              "properties": {"apiVersion": {"const": admin.VERSION}, "ok": {"const": True}, "result": obj}}
    failure = {"type": "object", "required": ["apiVersion", "ok", "error"],
               "properties": {"apiVersion": {"const": admin.VERSION}, "ok": {"const": False},
                              "error": {"type": "object", "required": ["code", "message"],
                                        "properties": {"code": {"type": "string"}, "message": {"type": "string"}}}}}

    def responses(output):
        return {"200": {"description": "Success", "content": {"application/json": {"schema": output}}},
                "default": {"description": "Request failed", "content": {"application/json": {"schema": failure}}}}

    paths = {PREFIX + "/openapi.json": {"get": {"operationId": "getOpenAPI", "responses": responses(obj)}},
             PREFIX + "/schema": {"get": {"operationId": "getAdminSchema", "responses": responses(result)}}}
    for name, spec in admin.OPERATIONS.items():
        method = "get" if spec["readOnly"] else "post"
        operation = {"operationId": name, "summary": spec["description"], "responses": responses(result),
                     "x-realm-availability": spec["availability"]}
        if method == "post":
            operation["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": spec["inputSchema"]}}}
        elif spec["inputSchema"]["properties"]:
            inputs = spec["inputSchema"]
            operation["parameters"] = [{"name": key, "in": "query", "required": key in inputs["required"],
                                        "schema": prop} for key,prop in inputs["properties"].items()]
        if name in READ_CONFIG:
            operation["responses"]["200"]["headers"] = {"ETag": {"schema":{"type":"string"},
                "description":"Current configuration revision, for conditional writes."}}
        if name in WRITE_CONFIG:
            operation["parameters"] = [{"name":"If-Match","in":"header","required":False,"schema":{"type":"string"},
                "description":"The ETag from the corresponding GET; a stale revision returns HTTP 412."}]
            operation["responses"]["412"] = operation["responses"]["default"]
        paths[OPERATIONS + name] = {method: operation}
    return {"openapi": "3.1.0", "info": {"title": "OF_SF operator API", "version": "1.0"},
            "security": [{"ownerToken": []}], "components": {"securitySchemes": {
                "ownerToken": {"type": "http", "scheme": "bearer"}}}, "paths": paths}


class AdminServer(ThreadingHTTPServer):
    daemon_threads = False  # Finish an owner's write before releasing the realm's lock.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, format, *args):
        pass  # Requests may contain owner-supplied paths or credentials.

    def send_error(self, code, message=None, explain=None):
        # HTTP parser errors normally echo the complete request line in an HTML page.
        self.fail(code, "http_error", self.responses.get(code, ("HTTP error",))[0])

    def reply(self, status, data, headers=()):
        raw = json.dumps(data, ensure_ascii=True, allow_nan=False).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(raw)

    def fail(self, status, code, message, headers=()):
        self.reply(status, {"apiVersion": admin.VERSION, "ok": False,
                            "error": {"code": code, "message": message}}, headers)

    def dispatch(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in self.server.hosts:
            return self.fail(400, "invalid_host", "Use the loopback administration address.")
        if self.path.startswith("/admin/") and not self.path.startswith(PREFIX):
            from realm_admin_console import ASSETS
            asset = ASSETS.get(self.path)
            if asset is not None:
                if self.command != "GET":
                    return self.fail(405, "invalid_method", "Use GET.", [("Allow","GET")])
                kind, raw = asset
                self.send_response(200)
                self.send_header("Content-Type", kind + "; charset=utf-8" if kind.startswith("text/") else kind)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Referrer-Policy","no-referrer")
                self.end_headers()
                self.wfile.write(raw)
                return
        credentials = self.headers.get_all("Authorization", [])
        if len(credentials) != 1 or not compare_digest(credentials[0].encode("latin-1"), self.server.bearer):
            return self.fail(401, "unauthorized", "Owner token required.",
                             [("WWW-Authenticate", 'Bearer realm="OF_SF"')])
        if self.path in (PREFIX + "/schema", PREFIX + "/openapi.json"):
            if self.command != "GET":
                return self.fail(405, "invalid_method", "Use GET.", [("Allow", "GET")])
            if self.path.endswith("/openapi.json"):
                return self.reply(200, openapi())
            return self.reply(200, admin.response_for(admin.schema))
        path, separator, query = self.path.partition("?")
        name = path.removeprefix(OPERATIONS)
        spec = admin.OPERATIONS.get(name) if path.startswith(OPERATIONS) else None
        if spec is None:
            return self.fail(404, "unknown_operation", "Unknown administration route.")
        expected = "GET" if spec["readOnly"] else "POST"
        if self.command != expected:
            return self.fail(405, "invalid_method", "Use " + expected + ".", [("Allow", expected)])
        values = {}
        if separator:
            if not spec["readOnly"] or not spec["inputSchema"]["properties"]:
                return self.fail(400, "invalid_request", "This operation takes no URL parameters.")
            try:
                for key, value in parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=16):
                    prop = spec["inputSchema"]["properties"].get(key)
                    if prop is None or key in values:
                        raise ValueError("Unknown or duplicate query parameter")
                    values[key] = value if prop["type"] == "string" else json.loads(value)
            except ValueError:
                return self.fail(400, "invalid_request", "Invalid query parameters.")
        if self.command == "POST":
            if self.headers.get_all("Transfer-Encoding"):
                return self.fail(400, "invalid_request", "Supply a fixed Content-Length.")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
                return self.fail(400, "invalid_request", "Supply a fixed Content-Length.")
            if len(lengths[0]) > 8 or int(lengths[0]) > admin.MAX_REQUEST:
                self.fail(413, "invalid_request", "Input exceeds 64 KiB.")
                # A close with unread request bytes can discard the error body on Windows.
                # Drain only a bounded, declared payload after sending the response.
                if len(lengths[0]) <= 8 and int(lengths[0]) <= 2 * admin.MAX_REQUEST:
                    self.rfile.read(int(lengths[0]))
                return
            if self.headers.get_content_type() != "application/json":
                return self.fail(415, "invalid_request", "Use application/json.")
            size = int(lengths[0])
            raw = self.rfile.read(size)
            if len(raw) != size:
                return self.fail(400, "invalid_request", "Incomplete request body.")
        tags = self.headers.get_all("If-Match", [])
        if tags and (name not in WRITE_CONFIG or len(tags) != 1 or not re.fullmatch(r'"[a-f0-9]{64}"', tags[0])):
            return self.fail(400, "invalid_request", "Invalid configuration revision.")
        response = admin.response_for(lambda: admin.execute(
            self.server.state, name, admin.decode_request(raw) if self.command == "POST" else values, self.server.request_stop,
            authorize=authorize_operation, expected=tags[0] if tags else None, live_capacity=self.server.live_capacity))
        headers = [("ETag", revision(response["result"]))] if response["ok"] and name in READ_CONFIG else []
        self.reply(200 if response["ok"] else STATUS[response["error"]["code"]], response, headers)

    do_GET = do_POST = dispatch


@contextmanager
def listener(configuration, state, request_stop, live_capacity=None):
    if configuration is None:
        yield None
        return
    port, bearer = configuration
    with AdminServer(("127.0.0.1", port), Handler) as server:
        server.live_capacity = live_capacity
        server.state = state
        server.request_stop = request_stop
        server.bearer = bearer
        server.hosts = {f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"}
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                                  name="realm-admin-http")
        thread.start()
        try:
            yield thread
        finally:
            server.shutdown()
            thread.join()
