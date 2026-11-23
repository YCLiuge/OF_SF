"""Player-owned server address book and SSH input parsing."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from prepare_client import save

BOOK = "server-addresses.json"


def ssh_address(text, port=None):
    text = text.strip()
    if not text or any(c.isspace() or c in "\\?#%" for c in text):
        raise ValueError("请输入 SSH 主机地址、别名或 user@host:端口。")
    if text.lower().startswith("ssh://"):
        text = text[6:]
    if "/" in text or text.count("@") > 1:
        raise ValueError("SSH 地址只接受用户名、主机和端口。")
    user, sep, host = text.rpartition("@")
    if sep and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", user):
        raise ValueError("SSH 用户名无效。")
    embedded = None
    if host.startswith("[") or host.count(":") == 1:
        parsed = urlsplit("//" + host)
        if not parsed.hostname or not re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9][A-Za-z0-9_.-]*)(?::[0-9]{1,5})?", host):
            raise ValueError("请使用 主机:端口，IPv6 加端口时写成 [IPv6]:端口。")
        host, embedded = parsed.hostname, parsed.port
    if ":" in host:
        host = str(ipaddress.IPv6Address(host))
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", host):
        raise ValueError("SSH 主机名或地址无效。")
    if port is not None and embedded is not None and int(port) != embedded:
        raise ValueError("地址内的 SSH 端口与独立端口栏不一致。")
    port = int(port) if port is not None else embedded
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("SSH 端口必须在 1 到 65535 之间。")
    return user + sep + host, port


def recent(home, endpoint, local, remote):
    entry = {"url": endpoint, "localServer": bool(local and not remote)}
    entry.update({k: v for k,v in remote.items() if k != "slot" and v is not None})
    target = Path(home) / BOOK
    history = json.loads(target.read_bytes()) if target.is_file() else []
    if not isinstance(history,list):
        raise ValueError("已保存的服务器地址文件格式错误。")
    def identity(row):
        return tuple(row.get(k) for k in ("url","localServer","remoteSsh","remoteSshPort","remoteProfile"))
    rows = [item for item in history if isinstance(item,dict) and identity(item)!=identity(entry)]
    save(target,[entry,*rows[:23]])

