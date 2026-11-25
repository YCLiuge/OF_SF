"""Authenticated player transport to an OF_SF realm's public TLS port."""
import hashlib
import ipaddress
import socket
import ssl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
import re


DEFAULT_PUBLIC_PORT = 18443
PIN_FILE = re.compile(r"of-leaf-([a-f0-9]{64})\.pem")


def entered_address(value):
    """Normalize a player's new input. Already saved canonical URLs retain HTTPS semantics."""
    value = value.strip()
    if not value or any(c.isspace() or c in "\\?#@%" for c in value):
        raise ValueError("请输入有效的区服 IP 或域名。")
    if "://" not in value:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            ip = None
        if ip and ip.version == 6:
            value = "[" + str(ip) + "]"
        value = "https://" + value
    url = urlsplit(value)
    if url.scheme != "https" or url.path not in ("", "/") or not url.hostname or url.netloc.endswith(":"):
        raise ValueError("请输入 IP 或 HTTPS 域名，可在地址后加端口。")
    host = url.hostname.encode("idna").decode("ascii")
    authority = "["+host+"]" if ":" in host else host
    port = url.port if url.port is not None else DEFAULT_PUBLIC_PORT
    return player_address("https://"+authority+":"+str(port))


def player_address(value):
    value = value.strip()
    if not value or any(c.isspace() or c in "\\?#@%" for c in value):
        raise ValueError("请输入服主提供的 HTTPS 域名和端口。")
    url = urlsplit(value)
    if (url.scheme != "https" or url.path not in ("", "/") or not url.hostname or
            not re.fullmatch(r"(?:[a-zA-Z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?", url.netloc)):
        raise ValueError("私服地址应为 https://主机:端口。")
    port = url.port if url.port is not None else 443
    if not 1 <= port <= 65535:
        raise ValueError("私服端口超出范围。")
    host = url.hostname
    authority = "[" + host + "]" if ":" in host else host
    return "https://" + authority + (":" + str(port) if port != 443 else "")


class PlayerTlsChannels:
    def __init__(self, address, ca_file=None, *, account_port=18881, game_port=11033):
        url = urlsplit(player_address(address))
        self.host, self.port = url.hostname, url.port or 443
        self.routes = {account_port: b"OF-SF/1 ACCOUNT\n", game_port: b"OF-SF/1 GAME\n"}
        self.pin = None
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        if ca_file:
            ca = Path(ca_file)
            if not ca.is_absolute() or not ca.is_file() or ca.stat().st_size > 1024 * 1024:
                raise ValueError("请选择服主提供的 PEM 根证书。")
            pinned = PIN_FILE.fullmatch(ca.name)
            if pinned:
                self.pin = pinned.group(1)
                self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self.context.minimum_version = ssl.TLSVersion.TLSv1_2
                self.context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            self.context.load_verify_locations(cafile=str(ca))

    @contextmanager
    def verified_socket(self):
        with socket.create_connection((self.host, self.port), timeout=12) as tcp:
            with self.context.wrap_socket(tcp, server_hostname=self.host) as secured:
                if self.pin and hashlib.sha256(secured.getpeercert(True)).hexdigest() != self.pin:
                    raise ssl.SSLCertVerificationError("区服证书指纹发生变化。")
                yield secured

    @contextmanager
    def open(self, port):
        route = self.routes[port]
        with self.verified_socket() as secured:
            secured.sendall(route)
            if route == b"OF-SF/1 GAME\n":
                from tls_game_stream import game_stream
                with game_stream(secured) as game:
                    yield game
            else:
                yield secured
