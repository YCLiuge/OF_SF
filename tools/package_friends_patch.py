"""Build a connection-free friends ZIP from a verified generic OF_SF player patch."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
import zipfile

from package_player_patch import CLIENT, INDEX, MARKER, SERVER, SERVER_TEMPLATE, TEMPLATE, TITLE


GUIDE = "朋友开箱.md"
OUTPUT = "OF_SF-Friends-1.1.12-Windows-x64.zip"
LOCAL_TEXTURES = {f"{SERVER}/resources/swirlnoisetexture/{n}.png" for n in range(1, 5)}
TOP = {TITLE, "玩家说明.md", "区服设置.cmd", MARKER, INDEX, TEMPLATE, f"{SERVER}/{SERVER_TEMPLATE}",
       f"{SERVER}/of.py", f"{SERVER}/LICENSE-of-ps.txt", *("client_tools/" + name for name in CLIENT)}
INSTRUCTIONS = """# OF_SF 朋友包

1. 安装并更新 TapTap 国服 PC 1.1.12。将补丁完整解压到独立目录，双击 **OF_SF 玩家补丁.exe**。
2. 从服主私下取得独立的 `.ofinvite` 文件，点「导入区服邀请」，确认后点「测试连接」。邀请以明文存储区服地址和可选的公开 CA，任何持有者都能读取。仅私下交给获准加入的人。
3. 选择原游戏目录。如果补丁和游戏不在同一磁盘，把「工作位置」选在游戏所在磁盘。
4. 新玩家填写本区账号和密码，点「仅创建账号」。要接续原角色，请在首次进入游戏前点「首次进游戏前导入存档」，先预览，再确认。完成后点「进入游戏」。想从头开始可直接进入。已有账号使用自己的账号密码登录。

启动和选服页面以「我的区服」显示连接。需要查看或修改连接明细时点「手动连接设置」。原版客户端保留发行方渠道验证。详细步骤见「玩家说明.md」。
"""


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


def build(source, expected_sha, output):
    source, output = source.resolve(), output.resolve()
    if not source.is_file() or output == source or source.suffix.lower() != ".zip":
        raise ValueError("Choose a generic input ZIP and a separate output")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) or sha_file(source) != expected_sha.lower():
        raise ValueError("Generic player ZIP does not match the supplied SHA-256")
    additions = {GUIDE: INSTRUCTIONS.encode("utf-8")}
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
        if "导入区服邀请".encode("utf-16le") not in src.read(TITLE):
            raise ValueError("This player executable predates private connection invitations")
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


    cli.add_argument("--output", type=Path, default=Path(OUTPUT))
    args = cli.parse_args()
    print(json.dumps(build(args.source, args.sha256, args.output), ensure_ascii=False))
