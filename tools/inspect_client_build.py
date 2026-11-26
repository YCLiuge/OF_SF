"""Inventory an installed Windows player from release headers and publisher manifests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from check_axe_assets import bundle_table

PREFIX = "PackageManifest_DefaultPackage"
STAMP = re.compile(r"[0-9]{4}(?:-[0-9]{2}){5}")
HEADER = b"OOY\0\x05\x002.0.0"
STOCK = Path("StreamingAssets/yoo/DefaultPackage")
CACHE = Path("yoo/DefaultPackage")


def bounded(path, limit):
    with path.open("rb") as src:
        data = src.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"文件过大：{path.name}")
    return data


def parse_manifest(blob, stamp):
    """Yoo asset rows map a logical asset to an index in the release's bundle table."""
    if not blob.startswith(HEADER):
        raise ValueError("不支持的 YooAsset 清单头或序列化版本")
    pos = len(HEADER) + 7

    def take(size):
        nonlocal pos
        end = pos + size
        if end > len(blob):
            raise ValueError("清单记录被截断")
        chunk = blob[pos:end]
        pos = end
        return chunk

    def number(size):
        return int.from_bytes(take(size), "little")

    def string():
        return take(number(2)).decode("utf-8")

    if (string(), string(), string()) != ("BuiltinBuildPipeline", "DefaultPackage", stamp):
        raise ValueError("清单中的构建管线、包名或版本与指针不匹配")
    count = number(4)
    if not 0 < count <= 500_000:
        raise ValueError("资源索引数无效")
    programs = {}
    largest_index = -1
    for _ in range(count):
        string()  # address
        name = string()
        string()  # asset GUID
        for _ in range(number(2)):
            string()  # tag
        index = number(4)
        largest_index = max(index, largest_index)
        if name.casefold().endswith((".patch.bytes", ".dll.bytes", ".dll", ".luac", ".lua")):
            if name in programs:
                raise ValueError("重复的脚本或补丁资源名")
            programs[name] = index
    bundle_count = number(4)
    if not 0 < bundle_count <= 100_000 or largest_index >= bundle_count:
        raise ValueError("资源指向无效的包索引")
    bundles = bundle_table(blob, bundle_count)
    mapping = {row.name: row.digest for row in bundles}
    if len(mapping) != bundle_count:
        raise ValueError("资源包存在重复名称")
    return count, {name: bundles[index] for name, index in programs.items()}, mapping


def manifest(folder, data, stock_data):
    stamp = bounded(folder / (PREFIX + ".version"), 64).decode("ascii").strip()
    if not STAMP.fullmatch(stamp):
        raise ValueError("资源版本指针格式错误")
    leaf = folder / (PREFIX + "_" + stamp)
    blob = bounded(leaf.with_suffix(".bytes"), 32 * 1024 * 1024)
    if bounded(leaf.with_suffix(".hash"), 80).decode("ascii").strip().lower() != hashlib.md5(blob).hexdigest():
        raise ValueError(f"{stamp} 清单与发行方同目录的 .hash 不一致")
    count, scripts, mapping = parse_manifest(blob, stamp)
    published = {}
    for name, row in scripts.items():
        cache = data / CACHE / "CacheFiles" / row.digest[:2] / row.digest / "__data"
        stock = stock_data / STOCK / (row.digest + ".bundle")
        available = None
        observed = {}
        for kind, path in (("cache", cache), ("builtin", stock)):
            if not path.is_file():
                continue
            if path.stat().st_size != row.size:
                observed[kind] = "size_mismatch"
                continue
            with path.open("rb") as f:
                digest = hashlib.file_digest(f, "md5").hexdigest()
            if digest == row.digest:
                available = kind
                break
            observed[kind] = digest
        published[name] = {"container_md5": row.digest, "container_bytes": row.size, "verified_local": available,
                           "mismatches": observed}
    return {"version": stamp, "sha256": hashlib.sha256(blob).hexdigest(), "assets": count,
            "bundles": len(mapping), "indexed_code_assets": published}, mapping


def inspect(root, extra=None):
    if not ((root / "launcher.exe").is_file() and (root / "launcher_Data").is_dir()):
        root = root / "Data/download/games/11113/install"
    data = root / "launcher_Data"
    if not (root / "launcher.exe").is_file() or not data.is_dir():
        raise ValueError("未找到 launcher.exe 和 launcher_Data，请选择游戏安装目录")
    # UnityFS signature, big-endian format, minimum player version, exact editor revision.
    with (data / "data.unity3d").open("rb") as file:
        header = file.read(128)
    if not header.startswith(b"UnityFS\0") or len(header) < 16:
        raise ValueError("不支持的 UnityFS 头")
    fields = header[12:].split(b"\0", 2)
    if len(fields) != 3 or not re.fullmatch(rb"\d+\.\d+\.\d+[a-zA-Z0-9.]+", fields[1]):
        raise ValueError("UnityFS 头没有可识别的引擎版本")
    engine = fields[1].decode("ascii")
    assembly = json.loads(bounded(data / "ScriptingAssemblies.json", 2 * 1024 * 1024))["names"]
    if not isinstance(assembly, list) or any(not isinstance(n, str) for n in assembly):
        raise ValueError("不支持的脚本程序集清单")
    metadata = data / "il2cpp_data/Metadata/global-metadata.dat"
    meta_version = None
    if metadata.is_file():
        with metadata.open("rb") as f:
            raw = f.read(8)
        if len(raw) == 8:
            magic, version = struct.unpack("<II", raw)
            if magic == 0xFAB11BAF and 1 <= version <= 99:
                meta_version = version
    managed_dll = (data / "Managed/Assembly-CSharp.dll").is_file()
    if (root / "GameAssembly.dll").is_file() and metadata.is_file():
        backend = "IL2CPP"
    elif managed_dll and (root / "MonoBleedingEdge").is_dir():
        backend = "Mono"
    else:
        backend = "undetermined"
    built, base_mapping = manifest(data / STOCK, data, data)
    version = bounded(root / "version", 64).decode("ascii").strip()
    if version != built["version"]:
        raise ValueError("客户端 version 和内置清单的版本不同")
    selected_data = data
    if extra is not None:
        selected_data = extra / "launcher_Data"
        overlay_base, _ = manifest(selected_data / STOCK, selected_data, data)
        if overlay_base["sha256"] != built["sha256"]:
            raise ValueError("外部缓存来自不同的基础清单")
    directory = selected_data / CACHE / "ManifestFiles"
    cache = None
    effective = base_mapping
    if (directory / (PREFIX + ".version")).is_file():
        cache, effective = manifest(directory, selected_data, data)
    elif extra is not None:
        raise ValueError("为外部缓存指定的目录缺少活动清单指针")
    source = {"assets": (root / "Assets").is_dir(), "editor_version": (root / "ProjectSettings/ProjectVersion.txt").is_file(),
              "packages_manifest": (root / "Packages/manifest.json").is_file()}
    info = {"unity": engine, "backend": backend, "metadata_header_version": meta_version,
            "declared_assemblies": len(assembly), "managed_game_dll": managed_dll,
            "runtime_markers": sorted(n for n in assembly if n in {"IFix.Core.dll","YooAsset.dll","HybridCLR.Runtime.dll",
                                       "ILRuntime.dll","SingularityGroup.HotReload.Runtime.Public.dll"}),
            "project_markers": source, "builtin": built, "cache": cache,
            "cache_source": "provided" if extra is not None else "installation" if cache else None}
    return info, effective


def compare(base, current):
    shared = base.keys() & current.keys()
    return {"unchanged": sum(base[k] == current[k] for k in shared), "changed": sum(base[k] != current[k] for k in shared),
            "added": len(current.keys() - base.keys()), "removed": len(base.keys() - current.keys())}


def main():
    ap = argparse.ArgumentParser(description="只读对照本机 Windows 客户端发行清单，以第一个样本为比较基准。")
    ap.add_argument("--client", action="append", nargs=2, required=True, metavar=("标记","安装目录"))
    ap.add_argument("--cache", action="append", nargs=2, default=[], metavar=("标记","另存的客户端资源根目录"))
    args = ap.parse_args()
    clients, overlays = dict(args.client), dict(args.cache)
    if len(clients) != len(args.client) or len(overlays) != len(args.cache) or not overlays.keys() <= clients.keys():
        ap.error("样本名必须唯一，每个缓存须指向一个已列出的样本")
    rows, differences = {}, {}
    first = None
    for name, folder in clients.items():
        try:
            rows[name], current = inspect(Path(folder), Path(overlays[name]) if name in overlays else None)
        except (OSError, ValueError, KeyError, struct.error) as err:
            ap.exit(1, f"{name} 检查失败：{err}\n")
        if first is None:
            first = current
        else:
            differences[name] = compare(first, current)
    print(json.dumps({"schema": 1, "clients": rows, "reference": next(iter(clients)), "effective_bundle_diff": differences},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.stdout.encoding:
        sys.stdout.reconfigure(encoding="utf-8")
    main()
