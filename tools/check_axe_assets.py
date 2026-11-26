"""Read and verify the mounted PC client's battle-axe and logging-axe bundles."""
import argparse
from pathlib import Path
import re
import struct
import sys
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "OverField-Server"
if not SERVER.is_dir():
    SERVER = ROOT / "kit" / "OverField-Server"
sys.path.insert(0, str(SERVER / "app"))

from game_dispatch import Assets, HASH, PREFIX


class Bundle(NamedTuple):
    name: str
    digest: str
    size: int
    dependencies: tuple


# The CN PC build's five ground attack families, aerial attack and logging tool.
SKILL_GROUPS = ("20201", "20203", "20211")
MODELS = {
    "assets_resbundle_model_weapon_typehit_greataxe.bundle",
    "assets_resbundle_model_weapon_typelife_loggingaxe.bundle",
}
REQUIRED = {"2020100", "2020101", "2020102", "2021100", "2021101", "2021102",
            "2020300", "2020310", "2020110", "2020280", "1002000"}


def bundle_table(blob, count):
    """Read YooAsset's length-prefixed bundle records after the asset path table."""
    first = HASH.search(blob)
    if first is None:
        raise ValueError("清单没有资源包表")
    end = first.start() + len(b".bundle")
    offset = blob.rfind(struct.pack("<I", count), max(0, end - 65541), first.start())
    if offset < 0 or offset + 6 + struct.unpack_from("<H", blob, offset + 4)[0] != end:
        raise ValueError("无法定位受支持清单的资源包表")
    pos = offset + 4

    def number(code):
        nonlocal pos
        value, = struct.unpack_from("<" + code, blob, pos)
        pos += struct.calcsize("<" + code)
        return value

    def string():
        nonlocal pos
        size = number("H")
        value = blob[pos:pos + size].decode("utf-8")
        pos += size
        return value

    table = []
    for _ in range(count):
        name = string()
        number("I")  # Unity CRC
        digest = string()
        string()  # file CRC
        size = number("Q")
        number("B")  # load method
        for _ in range(number("H")):
            string()  # bundle tag
        dependencies = tuple(number("I") for _ in range(number("H")))
        if not re.fullmatch(r"[a-f0-9]{32}", digest) or any(i >= count for i in dependencies):
            raise ValueError("清单包含未知资源摘要或越界依赖")
        table.append(Bundle(name, digest, size, dependencies))
    if pos != len(blob):
        raise ValueError("资源包表长度与清单不符")
    return table


def audit(client_root):
    mount = Assets(client_root)
    with mount.open_stock(f"{PREFIX}_{mount.patch}.bytes") as stream:
        table = bundle_table(stream.read(), mount.count)
    seeds, skills = set(), set()
    for index, bundle in enumerate(table):
        match = re.fullmatch(r"assets_resbundle_spell_active_(\d+)\.bundle", bundle.name)
        if match:
            skill = match.group(1)
            if skill.startswith(SKILL_GROUPS) or skill in {"1002000", "2020280"}:
                seeds.add(index)
                skills.add(skill)
        elif bundle.name in MODELS:
            seeds.add(index)
    if not REQUIRED.issubset(skills) or not MODELS.issubset(table[i].name for i in seeds):
        raise ValueError("清单缺少预期的大斧或伐木斧资源")
    pending = list(seeds)
    included = {}
    while pending:
        bundle = table[pending.pop()]
        if bundle.name in included:
            continue
        try:
            with mount.open_stock(bundle.digest + ".bundle") as stream:
                if stream.seek(0, 2) != bundle.size:
                    raise ValueError("文件长度不符")
        except (OSError, ValueError) as exc:
            raise ValueError(f"未找到通过清单校验的文件 {bundle.name} ({bundle.digest})") from exc
        included[bundle.name] = bundle
        pending.extend(bundle.dependencies)
    total = sum(bundle.size for bundle in included.values())
    print(f"{mount.patch}  已校验 {len(seeds)} 个入口、含依赖共 {len(included)} 包、{total / 2**20:,.2f} MiB。")
    return {name: bundle.digest for name, bundle in included.items()}


def main():
    parser = argparse.ArgumentParser(description="只读核对斧头普攻、音效、动画及伐木资源。多个目录将对照第一份。")
    parser.add_argument("client", nargs="+", type=Path, help="含 launcher.exe 的游戏目录")
    args = parser.parse_args()
    baseline = None
    matches = True
    try:
        for client in args.client:
            checked = audit(client)
            if baseline is None:
                baseline = checked
                continue
            changes = sorted(name for name in baseline.keys() | checked.keys() if baseline.get(name) != checked.get(name))
            print(f"与首份目录有 {len(changes)} 个文件不同。")
            for name in changes:
                print(f"  {name}")
            matches = matches and not changes
    except (OSError, ValueError, struct.error) as exc:
        print(f"核对未通过。{exc}", file=sys.stderr)
        return 1
    return 0 if matches else 2


if __name__ == "__main__":
    sys.exit(main())

