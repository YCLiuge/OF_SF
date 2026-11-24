"""Authenticated player transport to an OF_SF realm's public TLS port."""
import socket
import ssl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
import re


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
        self.context = ssl.create_default_context()
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        if ca_file:
            ca = Path(ca_file)
            if not ca.is_absolute() or not ca.is_file() or ca.stat().st_size > 1024 * 1024:
                raise ValueError("请选择服主提供的 PEM 根证书。")
            self.context.load_verify_locations(cafile=str(ca))

    @contextmanager
    def open(self, port):
        route = self.routes[port]
        with socket.create_connection((self.host, self.port), timeout=12) as tcp:
            with self.context.wrap_socket(tcp, server_hostname=self.host) as secured:
                secured.sendall(route)
                if route == b"OF-SF/1 GAME\n":
                    from tls_game_stream import game_stream
                    with game_stream(secured) as game:
                        yield game
                else:
                    yield secured
