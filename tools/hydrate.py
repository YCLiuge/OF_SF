"""Restore hash-checked, untracked build inputs from local OverField portable packages."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile


SERVER = "OverField-Server"
TEMPLATE = "client_tools/native_endpoint_template.json.bz2"
TEMPLATE_SHA256 = "f94bd99666f7f8f5fdaa564c689e90368e49b31abf620261870b988a5bab2f14"
SERVER_TEMPLATE = "docs/native-config/dispatch-delta.json.bz2"
SERVER_TEMPLATE_SHA256 = "7d1ae40477f024363aabc9cd3960eeb4e1314186b254c2871b79343b72bbb19f"
REPO = Path(__file__).resolve().parents[1]
GROUPS = ("runtime-win64", "runtime-linux", "vendor/common", "vendor/win64", "vendor/linux-x86_64")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def input_records(repo):
    with gzip.open(Path(__file__).with_name("portable-inputs.json.gz"), "rt", encoding="utf-8") as stream:
        records = json.load(stream)
    records[SERVER_TEMPLATE] = {"sha256": SERVER_TEMPLATE_SHA256, "mode": 0o644}
    with (repo / SERVER / "resource-sources.json").open(encoding="utf-8") as stream:
        for name, entry in json.load(stream)["files"].items():
            if not name.startswith("resources/"):
                raise ValueError(f"Unexpected game resource path {name}")
            records[name] = {"sha256": entry["sha256"], "mode": 0o644}
    return records


def local_path(root, name):
    # Keep parent directory links from redirecting reads or writes. The final
    # name may be a pinned Linux runtime symlink and is verified separately.
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or ".." in parts or "\\" in name or ":" in name:
        raise ValueError(f"Unexpected relative path {name}")
    path = root.joinpath(*parts)
    if path.parent.resolve() != path.parent:
        raise ValueError(f"Redirected directory {path.parent}")
    return path


def verify(path, record):
    if "link" in record:
        if not path.is_symlink() or os.readlink(path).replace("\\", "/") != record["link"]:
            raise ValueError(f"Unexpected symbolic link {path}")
    elif path.is_symlink() or not path.is_file() or digest(path) != record["sha256"]:
        raise ValueError(f"File is missing or has changed: {path}")


def copy_checked(source, target, record):
    if target.exists() or target.is_symlink():
        verify(target, record)
        if "link" not in record:
            target.chmod(record["mode"])
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if "link" in record:
        target.symlink_to(record["link"])
        return
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        shutil.copyfile(source, temp)
        verify(temp, record)
        temp.chmod(record["mode"])
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def hydrate(repo, bases, client_kit):
    records = input_records(repo)
    inputs = {}
    for base in bases:
        base = base.resolve(strict=True)
        if (base / SERVER).is_dir():
            if client_kit is None and (base / TEMPLATE).is_file():
                client_kit = base
            base = (base / SERVER).resolve(strict=True)
        if not (base / "resource-sources.json").is_file():
            raise ValueError(f"Expected an extracted server package in {base}")
        groups = [group for group in GROUPS if (base / group).is_dir()]
        if "vendor/common" not in groups or not any(group.startswith("runtime-") for group in groups):
            raise ValueError(f"Portable runtime or common dependencies missing in {base}")
        for name, record in records.items():
            if name == SERVER_TEMPLATE or name.startswith("resources/") or any(name.startswith(group + "/") for group in groups):
                path = local_path(base, name)
                if name == "resources/operator/item-names.zh.json" and not path.exists():
                    path = local_path(repo / SERVER, name)
                    if not path.exists():
                        raise ValueError("Run tools/export_reward_names.py with String_Simplified.json before hydrating an older base")
                verify(path, record)
                inputs[SERVER + "/" + name] = (path, record)
    if client_kit is not None:
        path = local_path(client_kit.resolve(strict=True), TEMPLATE)
        record = {"sha256": TEMPLATE_SHA256, "mode": 0o644}
        verify(path, record)
        inputs[TEMPLATE] = (path, record)
    # Validate existing files before writing so an incompatible checkout is
    # reported before any part of a new portable input set is installed.
    for name, (_, record) in inputs.items():
        dest = local_path(repo, name)
        if dest.exists() or dest.is_symlink():
            verify(dest, record)
    for name, (path, record) in inputs.items():
        copy_checked(path, local_path(repo, name), record)
    print(f"Verified {len(inputs)} local build inputs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--base", type=Path, action="append", default=[],
                        help="Extracted platform server directory or installed client kit (repeatable)")
    parser.add_argument("--client-kit", type=Path, help="Kit containing the native endpoint template")
    args = parser.parse_args()
    if not args.base and args.client_kit is None:
        parser.error("provide at least one --base or --client-kit")
    try:
        hydrate(args.repo.resolve(strict=True), args.base, args.client_kit)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")
