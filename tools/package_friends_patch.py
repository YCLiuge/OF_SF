"""Add a portable first-run realm preset to a verified generic OF_SF player ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import zipfile

from package_player_patch import CLIENT, INDEX, MARKER, SERVER, SERVER_TEMPLATE, TEMPLATE, TITLE


PRESET = "realm-preset.json"
CERTIFICATE = "realm-ca.pem"
GUIDE = "朋友开箱.md"
OUTPUT = "OF_SF-Friends-1.1.12-Windows-x64.zip"
LOCAL_TEXTURES = {f"{SERVER}/resources/swirlnoisetexture/{n}.png" for n in range(1, 5)}
TOP = {TITLE, "玩家说明.md", "区服设置.cmd", MARKER, INDEX, TEMPLATE, f"{SERVER}/{SERVER_TEMPLATE}",
       f"{SERVER}/of.py", f"{SERVER}/LICENSE-of-ps.txt", *("client_tools/" + name for name in CLIENT)}
INSTRUCTIONS = """# OF_SF 朋友包

1. 将整个 ZIP 解压到独立目录，双击 **OF_SF 玩家补丁.exe**。区服地址和服主 CA 已预填，点「测试连接」可在登录前检查。
2. 选择已更新到国服 PC 1.1.12 的 TapTap 安装目录。如果解压目录在另一块磁盘，请把「工作位置」选在游戏所在磁盘。
3. 第一次游玩可勾选「创建新私服账号」，填写自己新设的账号和密码，点「进入游戏」。已有本区账号可直接登录。游戏名可在游戏中设置。
4. 游戏使用原版客户端，保留发行方渠道验证。要更换区服，可双击「区服设置.cmd」。随包 CA 只供所选区服的补丁连接使用，系统无需安装证书。

跨盘安装、导入角色进度和卸载步骤见同目录「玩家说明.md」。
"""


def canonical_endpoint(value):
    if any(c.isspace() or c in "\\@%?#" for c in value):
        raise ValueError("Expected an HTTPS endpoint without credentials, path or query")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.path not in ("", "/") or not parts.hostname or
            not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?", parts.netloc)):
        raise ValueError("Expected https://host[:port]")
    port = 443 if parts.port is None else parts.port
    if not 1 <= port <= 65535:
        raise ValueError("Invalid TLS port")
    host = parts.hostname
    if ":" in host:
        host = "[" + host + "]"
    return "https://" + host + (f":{port}" if port != 443 else "")


def public_ca(path):
    if path.stat().st_size > 65536:
        raise ValueError("Expected one public PEM CA certificate")
    data = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----", data):
        raise ValueError("Expected one public PEM certificate, without keys or other text")
    der = ssl.PEM_cert_to_DER_cert(data)
    pem = ssl.DER_cert_to_PEM_cert(der)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=pem)
    if ctx.cert_store_stats()["x509_ca"] != 1:
        raise ValueError("PEM certificate is not a CA")
    return pem.encode("ascii")


def allowed_name(name):
    parts = PurePosixPath(name).parts
    if (not parts or name != "/".join(parts) or name.startswith("/") or any(
            part in (".", "..") or part.endswith((" ", ".")) or re.search(r'[\\:*?"<>|\x00-\x1f]', part)
            for part in parts)):
        return False
    if name in TOP or name in LOCAL_TEXTURES:
        return True
    if name.startswith(SERVER + "/app/"):
        return name.endswith(".py") and "__pycache__" not in parts
    if name.startswith(SERVER + "/resources/data/"):
        return name.endswith(".json") and len(parts) == 4
    if not name.startswith((SERVER + "/runtime-win64/", SERVER + "/vendor/common/", SERVER + "/vendor/win64/")):
        return False
    return not name.lower().endswith((".pem", ".key", ".pfx", ".p12", ".log", ".db", ".sqlite", ".sqlite3",
                                      ".unity3d", ".bundle", ".assets", ".pcap", ".dmp", ".iso"))


def metadata(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_entry(archive, name, data):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=6)


def build(source, expected_sha, endpoint, ca, output):
    source, ca, output = (p.resolve() for p in (source, ca, output))
    if not source.is_file() or output in {source, ca} or source.suffix.lower() != ".zip":
        raise ValueError("Choose a generic input ZIP and a separate output")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) or sha_file(source) != expected_sha.lower():
        raise ValueError("Generic player ZIP does not match the supplied SHA-256")
    endpoint = canonical_endpoint(endpoint)
    additions = {PRESET: metadata({"version": 1, "publicTls": endpoint, "caFile": CERTIFICATE}),
                 CERTIFICATE: public_ca(ca), GUIDE: INSTRUCTIONS.encode("utf-8")}
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".of-friends-", dir=output.parent) as work, zipfile.ZipFile(source) as src:
        names = src.namelist()
        if len({k.casefold() for k in names}) != len(names) or not all(allowed_name(k) for k in names):
            raise ValueError("Unexpected member in generic player ZIP")
        index = json.loads(src.read(INDEX))
        if (index.get("version") != 1 or not isinstance(index.get("files"), dict)
                or not TOP.issubset(names) or set(index["files"]) | {INDEX, MARKER} != set(names)
                or {INDEX, MARKER}.intersection(index["files"])
                or json.loads(src.read(MARKER)) != {"product": "OF_SF-Player-1.1.12"}):
            raise ValueError("Invalid generic player manifest")
        if PRESET.encode("utf-16le") not in src.read(TITLE):
            raise ValueError("This player executable predates portable realm presets")
        temp = Path(work) / "friends.zip"
        retained = {}
        with zipfile.ZipFile(temp, "w") as dst:
            for name in sorted(index["files"]):
                data = src.read(name)
                sha = hashlib.sha256(data).hexdigest()
                if sha != index["files"][name]:
                    raise ValueError("Generic manifest hash mismatch for " + name)
                if name not in LOCAL_TEXTURES:
                    write_entry(dst, name, data)
                    retained[name] = sha
            for name, data in sorted(additions.items()):
                write_entry(dst, name, data)
                retained[name] = hashlib.sha256(data).hexdigest()
            write_entry(dst, MARKER, src.read(MARKER))
            write_entry(dst, INDEX, metadata({"version": 1, "files": retained}))
        sha = sha_file(temp)
        side = Path(work) / "sha256"
        side.write_bytes((sha + "  " + output.name + "\n").encode("utf-8"))
        size = temp.stat().st_size
        os.replace(temp, output)
        os.replace(side, output.with_name(output.name + ".sha256"))
    return {"input_sha256": expected_sha.lower(), "sha256": sha, "bytes": size,
            "files": len(retained) + 2, "added": sorted(additions), "omitted": sorted(set(names) & LOCAL_TEXTURES),
            "updated": [INDEX]}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source", required=True, type=Path, help="Immutable generic player ZIP, never an installed directory")
    cli.add_argument("--sha256", required=True, help="Verified SHA-256 of that generic ZIP")
    cli.add_argument("--endpoint", required=True, help="Public HTTPS realm address")
    cli.add_argument("--ca", required=True, type=Path, help="Public PEM CA for this realm")
    cli.add_argument("--output", type=Path, default=Path(OUTPUT))
    args = cli.parse_args()
    print(json.dumps(build(args.source, args.sha256, args.endpoint, args.ca, args.output), ensure_ascii=False))
