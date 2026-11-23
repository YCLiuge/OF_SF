"""Rebuild a platform server archive from tracked source and locally held portable inputs."""
import argparse
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

from hydrate import REPO, SERVER, SERVER_TEMPLATE, digest, input_records, local_path


ROOT_FILES = {"of.py", "部署说明.md", "Create-Player.cmd", "Start-Server.cmd", "start.sh",
              ".dockerignore", "Dockerfile", "compose.yaml", "LICENSE-of-ps.txt", "resource-sources.json"}


def tracked_source(repo, linux):
    raw = subprocess.check_output(["git", "-C", str(repo), "ls-files", "-z", "--", SERVER])
    files = []
    for path in raw.decode("utf-8").split("\0"):
        if not path:
            continue
        name = path.removeprefix(SERVER + "/")
        parts = set(name.casefold().split("/"))
        if name == SERVER_TEMPLATE or parts & {"state", "profiles", "logs", "__pycache__", ".env"}:
            continue
        if name.casefold().endswith((".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo",
                                    ".pcap", ".pcapng", ".dmp", ".pem", ".key")):
            continue
        if name in ROOT_FILES or name.startswith(("app/", "docs/", "tests/")):
            if linux and name.lower().endswith(".cmd"):
                continue
            file = local_path(repo, path)
            if file.is_symlink() or not file.is_file():
                raise ValueError(f"Tracked source missing or redirected: {file}")
            files.append((name, file))
    found = {name for name, _ in files}
    if not {"of.py", "start.sh", "resource-sources.json", "app/game_dispatch.py"} <= found:
        raise ValueError("Expected tracked server source in the checkout")
    return files


def archive_inputs(base, linux):
    if linux:
        with tarfile.open(base, "r:gz") as archive:
            for member in archive:
                if member.name.startswith(SERVER + "/"):
                    name = member.name[len(SERVER) + 1:]
                    yield name, member.linkname if member.issym() else None, (
                        lambda m=member: archive.extractfile(m).read() if m.isfile() else None)
    else:
        with zipfile.ZipFile(base) as archive:
            for member in archive.infolist():
                if member.filename.startswith(SERVER + "/"):
                    name = member.filename[len(SERVER) + 1:]
                    yield name, None, (lambda m=member: archive.read(m))


def rebuild(repo, base, output):
    linux = base.name.endswith(".tar.gz")
    extension = ".tar.gz" if linux else ".zip"
    if not base.name.endswith(extension) or not output.name.endswith(extension) or base == output:
        raise ValueError("Choose a distinct output with the same .zip or .tar.gz suffix as the base")
    groups = ("resources/", "vendor/common/", "vendor/linux-x86_64/")
    groups += ("runtime-linux/",) if linux else ("runtime-win64/", "vendor/win64/")
    expected = {name: data for name, data in input_records(repo).items()
                if name == SERVER_TEMPLATE or name.startswith(groups)}
    sources = tracked_source(repo, linux)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".of-build-") as work:
        target = Path(work) / "payload"
        with ExitStack() as stack:
            tar = stack.enter_context(tarfile.open(target, "w:gz", compresslevel=6)) if linux else None
            zip_out = None if linux else stack.enter_context(zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED))
            checksums = {}

            def add(name, data, mode, link=None):
                key = SERVER + "/" + name
                if tar is not None:
                    item = tarfile.TarInfo(key)
                    item.mode = mode
                    if link is not None:
                        item.type, item.linkname = tarfile.SYMTYPE, link
                        tar.addfile(item)
                    else:
                        item.size = len(data)
                        tar.addfile(item, io.BytesIO(data))
                else:
                    item = zipfile.ZipInfo(key)
                    item.create_system = 3
                    item.external_attr = (0o100000 | mode) << 16
                    zip_out.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED)
                if not name.startswith("runtime-") and link is None:
                    checksums[name] = hashlib.sha256(data).hexdigest()

            seen = set()
            for name, link, read in archive_inputs(base, linux):
                if name not in expected:
                    continue
                if name in seen:
                    raise ValueError(f"Duplicate portable input {name}")
                seen.add(name)
                record = expected[name]
                if link != record.get("link"):
                    raise ValueError(f"Unexpected symbolic link {name}")
                data = None if link is not None else read()
                if link is None and (data is None or hashlib.sha256(data).hexdigest() != record["sha256"]):
                    raise ValueError(f"File is missing or has changed: {name}")
                add(name, data, record["mode"], link)
            missing = expected.keys() - seen
            if missing:
                raise ValueError(f"Portable base is missing {len(missing)} files, including {min(missing)}")
            for name, path in sources:
                add(name, path.read_bytes(), 0o755 if name == "start.sh" else 0o644)
            index = json.dumps(checksums, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            add("release-source-digests.json", index.encode("utf-8"), 0o644)
        checksum = digest(target)
        sidecar = Path(work) / "sha256"
        sidecar.write_text(f"{checksum}  {output.name}\n", encoding="utf-8")
        os.replace(target, output)
        os.replace(sidecar, output.with_name(output.name + ".sha256"))
    print(f"{output}  {checksum}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        rebuild(args.repo.resolve(strict=True), args.base.resolve(strict=True), args.output.resolve())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{error}\n")
