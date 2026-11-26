"""Private OverField profile discovery. Serves the independent local profile realm."""
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
from urllib.parse import urlsplit

from player_gateway_peer import account_body_limit
from game_dispatch import handle_game_dispatch
from realm_maintenance import Maintenance, public_get
from realm_accounts import AccountService, AccountError, ROUTES as ACCOUNT_ROUTES
from local_identity import InvalidProfile, LocalIdentity, NicknameUnavailable


REALM = "overfield-local-v1"


def _hostname(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a host name or IP address")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        if len(value) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                                       for part in value.split(".")):
            raise ValueError("Expected a host name or IP address")
        return value.lower()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


class ProfileHandler(BaseHTTPRequestHandler):
    server_version = "OverFieldLocal"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(7)
        if self.server.tls_context is not None:
            self.request = self.server.tls_context.wrap_socket(self.request, server_side=True)
        super().setup()

    def log_message(self, fmt, *args):
        # Never log arbitrary request paths, profile keys, or other client inputs.
        pass

    def _reply(self, status, data):
        data = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _accept(self):
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or self.headers.get("Origin") is not None:
            self._reply(403, {"error": "request rejected"})
            return False
        try:
            host = hosts[0]
            if any(c in host for c in "/\\@#?, \t"):
                raise ValueError()
            parsed = urlsplit("//" + host)
            _ = parsed.port
            if _hostname(parsed.hostname) not in self.server.hostnames:
                raise ValueError()
        except (TypeError, ValueError):
            self._reply(403, {"error": "unrecognized host"})
            return False
        if self.server.stages is not None:
            self.server.stages.http(self.path)
        return True

    def do_HEAD(self):
        if self._accept() and not public_get(self) and not handle_game_dispatch(self):
            self.send_error(404)

    def do_GET(self):
        if not self._accept():
            return
        if handle_game_dispatch(self):
            return
        if public_get(self):
            return
        if self.path == "/private/gifts":
            if any(k in self.headers for k in ("Authorization","Cookie","Proxy-Authorization")):
                self._reply(403,{"error":"public catalog does not take credentials"})
                return
            try:
                self._reply(200,self.server.accounts.gifts.catalog())
            except AccountError as exc:
                self._reply(exc.status,{"error":exc.code})
            return
        if self.path == "/local/v1/region":
            self._reply(200, self.server.region)
        elif self.path == "/healthz":
            native_session = os.environ.get("OF_NATIVE_SESSION", "")
            is_local = ipaddress.ip_address(self.client_address[0]).is_loopback
            value = native_session if is_local and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", native_session) else ""
            self._reply(200, {"ok": True, "service": REALM, "nativeBuild": "online-pc-1.1.12", "nativeSession": value})
        else:
            self._reply(404, {"error": "route not found"})

    def do_POST(self):
        if not self._accept():
            return
        if handle_game_dispatch(self):
            return
        if self.path != "/local/v1/accounts/attach" and self.path not in ACCOUNT_ROUTES and self.path not in ("/local/v1/profiles", "/local/v1/sessions"):
            self._reply(404, {"error": "route not found"})
            return
        if self.path.endswith("/profiles") and not self.server.registration:
            self._reply(403, {"error": "new profile registration is closed"})
            return
        lengths = self.headers.get_all("Content-Length") or []
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,7}", lengths[0]):
            self._reply(400, {"error": "expected fixed-length JSON body"})
            return
        length = int(lengths[0])
        if length > account_body_limit(self.path):
            self._reply(413, {"error": "body too large"})
            return
        if self.headers.get_content_type() != "application/json":
            self._reply(415, {"error": "expected application/json"})
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
            if not isinstance(body, dict):
                raise ValueError()
            if self.path == "/local/v1/accounts/attach":
                seats=getattr(self.server,"native_accounts",None)
                if not seats:
                    raise AccountError(403,"native_account_mode_disabled")
                seats.attach(self.connection,body)
                self._reply(200,{"ok":True})
                return
            if self.path in ACCOUNT_ROUTES:
                import player_gateway_peer as proof
                values = self.headers.get_all(proof.HEADER) or []
                source = None
                if values:
                    if len(values)==1:
                        source=proof.verify(self.server.accounts.gateway_peer_key,values[0],self.path,raw,
                                            self.client_address[0],self.server.server_address[0])
                    if source is None:
                        raise AccountError(403,"invalid_gateway_source")
                result = self.server.accounts.request(self.path,body,self.client_address[0],self.server.server_address[0],
                                                      self.server.tls_context is not None,self.server.account_registration,
                                                      gateway_source=source)
                self._reply(201 if self.path.endswith(("/register","/bind")) else 200,result)
                return
            if self.path == "/local/v1/profiles":
                if set(body) - {"nickname"} or ("nickname" in body and not isinstance(body["nickname"], str)):
                    raise ValueError()
                slot, key = self.server.identity.create_profile(body.get("nickname"))
                self._reply(201, {"realm": REALM, "slot": slot, "profile_key": key,
                                  "nickname": self.server.identity.profile_name(slot)})
                return
            if set(body) != {"slot", "profile_key"}:
                raise ValueError()
            ticket = self.server.identity.issue_ticket(body["slot"], body["profile_key"])
            self._reply(200, {"realm": REALM, "ticket": ticket, "expires_in": self.server.identity.ticket_ttl,
                              "region": self.server.region,
                              "nickname": self.server.identity.profile_name(body["slot"])})
        except AccountError as exc:
            self._reply(exc.status,{"error":exc.code})
        except NicknameUnavailable:
            self._reply(409, {"error": "nickname in use"})
        except InvalidProfile:
            self._reply(401, {"error": "invalid profile credentials"})
        except (ValueError, TypeError, RecursionError):
            self._reply(400, {"error": "invalid request for local realm"})
        except sqlite3.Error:
            self._reply(503, {"error": "profile storage unavailable"})


def create_profile_server(identity, *, bind_host="127.0.0.1", port=18881,
                          game_host="127.0.0.1", game_port=11033,
                          allowed_hosts=(), allow_registration=None, tls_context=None):
    """Build an HTTP server; game_host/port is the actual TCP endpoint to advertise."""
    bind = ipaddress.ip_address(bind_host)
    advertised = _hostname(game_host)
    if advertised in ("0.0.0.0", "::"):
        raise ValueError("Advertise a reachable game host")
    if not 0 <= port <= 65535 or not 1 <= game_port <= 65535:
        raise ValueError("invalid port")
    hosts = {_hostname(h) for h in allowed_hosts}
    if not bind.is_unspecified:
        hosts.add(bind.compressed)
    hosts.add(advertised)
    hosts.update(("localhost", "127.0.0.1", "::1"))
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if bind.version == 6 else socket.AF_INET
    service = Server((bind.compressed, port), ProfileHandler)
    service.identity = identity
    service.maintenance = Maintenance(identity)
    service.stages = None
    service.registration = bind.is_loopback if allow_registration is None else bool(allow_registration)
    account_policy = os.environ.get("OF_ACCOUNT_REGISTRATION")
    if account_policy not in (None,"open","closed"):
        service.server_close()
        raise ValueError("OF_ACCOUNT_REGISTRATION must be open or closed")
    service.accounts = AccountService(identity)
    service.account_registration = service.registration if account_policy is None else account_policy=="open"
    service.region = {"realm": REALM, "game_host": advertised, "game_port": game_port,
                      "game_protocol": "overfield-packethead-protobuf", "client_version": "1.1.12",
                        "save_import_features": ["pets", "field_progress", "public_uid_label", "fishing_rods", "profile_sign"],"account_features":["captcha_lookup_v1","password_change_v1"]}
    service.hostnames = frozenset(hosts)
    service.tls_context = tls_context
    return service


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="Private server state directory")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18881)
    parser.add_argument("--game-host", default="127.0.0.1")
    parser.add_argument("--game-port", type=int, default=11033)
    parser.add_argument("--http-host", action="append", default=[], help="Additional advertised HTTP host")
    parser.add_argument("--registration", choices=("open", "closed"))
    parser.add_argument("--create-profile", type=Path, help="Provision one new user to a new private JSON file and exit")
    parser.add_argument("--nickname", help="Optional local nickname for --create-profile")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("Supply both TLS certificate and private key paths")
    context = None
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert, args.tls_key)
    args.state.mkdir(parents=True, exist_ok=True)
    identity = LocalIdentity(args.state/"identity.sqlite3")
    if args.create_profile:
        file = args.create_profile
        fd = os.open(file, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out, identity.creating_profile(args.nickname) as record:
                slot, key, display = record
                json.dump({"realm": REALM, "slot": slot, "profile_key": key,
                           "nickname": display}, out, ensure_ascii=False)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            file.unlink(missing_ok=True)
            raise
        print("Private profile written to the requested file.")
        return
    if args.nickname is not None:
        parser.error("--nickname requires --create-profile")
    policy = None if args.registration is None else args.registration == "open"
    with create_profile_server(identity, bind_host=args.bind, port=args.port, game_host=args.game_host,
             game_port=args.game_port, allowed_hosts=args.http_host, allow_registration=policy, tls_context=context) as service:
        print(f"Profile service listening on {service.server_address[0]}:{service.server_port}",flush=True)
        try:
            service.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
