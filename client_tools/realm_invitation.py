"""Import a privately shared offline realm invitation into this player installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OverField-Server/app"))
from player_tls_channels import player_address

FORMAT = "OF_SF-Realm-Invitation"
MAX_BYTES = 65536


def read_small(path):
    with Path(path).open("rb") as src:
        data = src.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("文件过大。")
    return data


def public_ca(data):
    if not isinstance(data, str) or len(data) > MAX_BYTES:
        raise ValueError("证书格式无效。")
    if not data:
        return "", ""
    if not re.fullmatch(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----", data.strip()):
        raise ValueError("请选择单张公开 PEM 根证书。")
    der = ssl.PEM_cert_to_DER_cert(data.strip())
    pem = ssl.DER_cert_to_PEM_cert(der)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=pem)
    if ctx.cert_store_stats()["x509_ca"] != 1:
        raise ValueError("证书不具备 CA 签发用途。")
    return pem, hashlib.sha256(der).hexdigest().upper()


def parse(data):
    def unique(pairs):
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError("邀请包含重复字段。")
        return values

    form = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
    if (not isinstance(form, dict) or set(form) != {"format", "version", "endpoint", "caPem"} or
            form["format"] != FORMAT or type(form["version"]) is not int or form["version"] != 1 or
            not isinstance(form["endpoint"], str)):
        raise ValueError("不是受支持的区服邀请。")
    address = player_address(form["endpoint"])
    pem, fingerprint = public_ca(form["caPem"])
    return address, pem, fingerprint


def atomic(path, data):
    pending = tempfile.NamedTemporaryFile(prefix=".of-invite-", dir=path.parent, delete=False)
    name = Path(pending.name)
    try:
        with pending:
            pending.write(data)
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def configure(root, address, ca):
    settings = root / "player-settings.json"
    prefs = json.loads(settings.read_bytes()) if settings.is_file() else {}
    if not isinstance(prefs, dict) or not isinstance(prefs.get("servers", []), list):
        raise ValueError("原有玩家设置需要修复。")
    entry = {"publicTls": address, "caFile": ca}
    history = [row for row in prefs.get("servers", []) if isinstance(row, dict) and row.get("publicTls") != address]
    prefs.update(entry, servers=[entry, *history[:23]])
    atomic(settings, json_bytes(prefs))
    return {"ok": True, **entry}


def handle(root, form):
    action = form.get("action")
    if action == "export":
        folder = Path(os.environ["LOCALAPPDATA"]) / "OF_SF" / "PrivateConnections"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("区服邀请-" + uuid.uuid4().hex[:12] + ".ofinvite")
        export(root / "player-settings.json", target, selected=form.get("publicTls"))
        return {"ok": True, "path": str(target)}
    if action == "manual":
        address = form.get("publicTls")
        ca = form.get("caFile", "")
        if not isinstance(address, str) or not isinstance(ca, str):
            raise ValueError("连接资料格式无效。")
        address = player_address(address)
        if ca:
            path = Path(ca).resolve(strict=True)
            if not public_ca(read_small(path).decode("ascii"))[0]:
                raise ValueError("请选择有效的 CA 证书。")
            ca = str(path)
        return configure(root, address, ca)
    if action not in {"inspect", "import"}:
        raise ValueError("未知连接操作。")
    data = read_small(form["path"])
    address, pem, fingerprint = parse(data)
    sha = hashlib.sha256(data).hexdigest()
    if action == "inspect":
        return {"ok": True, "digest": sha, "fingerprint": fingerprint}
    if form.get("digest") != sha:
        raise ValueError("确认后邀请文件已改变，请重新选择。")
    ca_path = ""
    if pem:
        storage = root / "PrivateConnections"
        storage.mkdir(exist_ok=True)
        if storage.is_symlink() or getattr(storage, "is_junction", lambda: False)():
            raise ValueError("请使用本机普通目录保存区服信息。")
        cert = storage / (fingerprint + ".pem")
        if cert.exists() and cert.read_bytes() != pem.encode("ascii"):
            raise ValueError("本地证书文件发生变化。")
        if not cert.exists():
            atomic(cert, pem.encode("ascii"))
        ca_path = str(cert)
    return configure(root, address, ca_path)


def export(settings, output, selected=None):
    prefs = json.loads(settings.read_bytes())
    address = player_address(prefs["publicTls"])
    if selected is not None and selected != address:
        raise ValueError("请先保存所选连接。")
    ca = prefs.get("caFile")
    pem = public_ca(read_small(ca).decode("ascii"))[0] if ca else ""
    if ca and not pem:
        raise ValueError("本机配置的 CA 为空。")
    if output.suffix.lower() != ".ofinvite":
        raise ValueError("邀请文件须以 .ofinvite 结尾。")
    invitation = json_bytes({"format": FORMAT, "version": 1, "endpoint": address, "caPem": pem})
    # Never replace an existing personal file, and never copy account fields into the invitation.
    with output.open("xb") as target:
        target.write(invitation)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("ui", "export"))
    cli.add_argument("--settings", type=Path, default=ROOT / "player-settings.json")
    cli.add_argument("--output", type=Path)
    options = cli.parse_args()
    try:
        if options.action == "export":
            if not options.output:
                cli.error("--output is required for export")
            export(options.settings, options.output)
            print("已生成独立明文邀请。仅私下交给获准加入的人。")
        else:
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("连接操作输入过大。")
            print(json.dumps(handle(ROOT, json.loads(data)), ensure_ascii=True))
    except (KeyError, OSError, ValueError, TypeError, UnicodeError):
        if options.action == "ui":
            print('{"ok":false}')
        else:
            raise SystemExit("生成邀请失败。请检查本机连接资料、CA 和输出文件。")
