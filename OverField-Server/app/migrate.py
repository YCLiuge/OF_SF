"""Portable stopped-server backups for OverField. Shared with the supervisor via state_lock."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory
import unicodedata
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from state_lock import StateBusy, exclusive_state, is_link_or_reparse

FORMAT = "overfield-private-state/1"
DATABASES = {"identity.sqlite3", "game.sqlite"}
MANIFEST = "manifest.json"
CHUNK = 1024 * 1024
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
RESERVED.update(p + n for p in ("COM", "LPT") for n in "123456789¹²³")


def portable(name):
    if not isinstance(name, str) or not name:
        raise ValueError("Invalid archive filename")
    bits = name.split("/")
    if any(not a or a in (".", "..") or a[-1] in " ." or
           any(ord(c) < 32 or c in '<>:"|?*\\' for c in a) or
           a.split(".")[0].rstrip(" ").upper() in RESERVED for a in bits):
        raise ValueError("Unsafe or nonportable archive filename")
    return name


def validate_layout(dirs, files):
    if not isinstance(dirs, list) or not isinstance(files, dict) or not DATABASES <= files.keys():
        raise ValueError("Backup must contain both server databases")
    names = set()
    directory_names = set(dirs)
    for relative in [*dirs, *files]:
        portable(relative)
        key = unicodedata.normalize("NFC", relative).casefold()
        if key in names:
            raise ValueError("Conflicting backup paths")
        names.add(key)
        parent = relative.rpartition("/")[0]
        if parent and parent not in directory_names:
            raise ValueError("Missing parent or file/directory conflict")


def discover(root):
    folders, files = [], {}
    for entry in sorted(root.iterdir()):
        visit(root, entry, folders, files)
    validate_layout(folders, files)
    return folders, files


def visit(root, path, folders, files):
    info = path.lstat()
    if is_link_or_reparse(info):
        raise ValueError("Linked files are not supported in the state directory")
    relative = portable(path.relative_to(root).as_posix())
    if stat.S_ISDIR(info.st_mode):
        folders.append(relative)
        for entry in sorted(path.iterdir()):
            visit(root, entry, folders, files)
    elif stat.S_ISREG(info.st_mode):
        files[relative] = path
    else:
        raise ValueError("Unsupported special file in server state")


def check_db(path):
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\0":
            raise ValueError("Expected a valid SQLite database")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


def snapshot_db(original, output):
    with original.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\0":
            raise ValueError("Expected a valid SQLite database")
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(output)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE").fetchone()
    check_db(output)


def copy_hash(source, destination):
    digest = hashlib.sha256()
    length = 0
    while (data := source.read(CHUNK)):
        destination.write(data)
        digest.update(data)
        length += len(data)
    return {"size": length, "sha256": digest.hexdigest()}


def fsync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def inside_state(archive, data):
    if archive.is_relative_to(data):
        raise ValueError("Choose an archive path outside the data directory")


def publish_new_file(source, destination):
    # CreateFile/MoveFile refuses overwrite on Windows. On POSIX link is atomic
    # and fails if another process published the destination in the meantime.
    if os.name == "nt":
        source.rename(destination)
    else:
        os.link(source, destination)


def backup(directory, archive):
    archive = Path(archive).absolute()
    if archive.is_symlink() or archive.exists():
        raise FileExistsError("Backup destination already exists")
    with exclusive_state(directory) as root, exclusive_state(root, purpose="profiles"):
        if not root.is_dir():
            raise FileNotFoundError("State directory does not exist")
        archive = archive.parent.resolve() / archive.name
        inside_state(archive, root)
        folders, files = discover(root)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".of-state-snapshot-", dir=archive.parent) as work:
            work = Path(work)
            for db in sorted(DATABASES):
                snapshot_db(files[db], work / db)
                files[db] = work / db
                for suffix in ("-wal", "-shm", "-journal"):
                    files.pop(db + suffix, None)
            temp = work / "backup.zip"
            manifest = {"format": FORMAT, "dirs": folders, "files": {}}
            with ZipFile(temp, "x", ZIP_DEFLATED, allowZip64=True) as out:
                for relative, path in sorted(files.items()):
                    with path.open("rb") as a, out.open("data/" + relative, "w", force_zip64=True) as b:
                        manifest["files"][relative] = copy_hash(a, b)
                out.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=True, sort_keys=True))
            os.chmod(temp, 0o600)
            with temp.open("rb+") as f:
                os.fsync(f.fileno())
            publish_new_file(temp, archive)
            fsync_directory(archive.parent)
    return archive


def unique_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("Duplicate key in backup manifest")
        value[key] = child
    return value


def read_manifest(z):
    entries = {}
    for info in z.infolist():
        if info.filename != info.orig_filename or info.flag_bits & 1:
            raise ValueError("Invalid or encrypted backup member")
        if info.filename in entries:
            raise ValueError("Duplicate archive member")
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind not in (0, stat.S_IFREG) or info.is_dir():
            raise ValueError("Unexpected archive entry type")
        entries[info.filename] = info
    if MANIFEST not in entries:
        raise ValueError("Missing state backup manifest")
    manifest = json.loads(z.read(MANIFEST), object_pairs_hook=unique_object)
    if not isinstance(manifest,dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported state backup format")
    dirs = manifest.get("dirs")
    files = manifest.get("files")
    validate_layout(dirs, files)
    if set(entries) != {MANIFEST, *("data/" + f for f in files)}:
        raise ValueError("Archive members differ from manifest")
    for n, v in files.items():
        if not isinstance(v, dict) or type(v.get("size")) is not int or v["size"] != entries["data/"+n].file_size:
            raise ValueError("Invalid file size")
        if not isinstance(v.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", v["sha256"]):
            raise ValueError("Invalid file digest")
        if n.casefold() in {db + suffix for db in DATABASES for suffix in ("-journal", "-wal", "-shm")}:
            raise ValueError("Snapshot unexpectedly contains a database sidecar")
    return dirs, files


def unpack(z, root):
    dirs, files = read_manifest(z)
    for folder in sorted(dirs, key=lambda n: n.count("/")):
        (root/folder).mkdir(mode=0o700)
    for relative, expected in files.items():
        dest = root/relative
        with z.open("data/" + relative) as src, dest.open("xb") as out:
            actual = copy_hash(src, out)
            if actual != expected:
                raise ValueError("File content did not match the backup manifest")
            out.flush()
            os.fsync(out.fileno())
    for db in DATABASES:
        check_db(root/db)
    for folder in sorted(dirs, key=lambda n:-n.count("/")):
        fsync_directory(root/folder)
    fsync_directory(root)


def restore(archive, directory):
    archive = Path(archive).resolve(strict=True)
    with exclusive_state(directory) as root, exclusive_state(root, purpose="profiles"):
        inside_state(archive, root)
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise FileExistsError("Restore target must be absent or empty")
        with TemporaryDirectory(prefix=".of-state-restore-", dir=root.parent) as parent:
            stage = Path(parent)/"data"
            stage.mkdir(mode=0o700)
            with ZipFile(archive) as source:
                unpack(source, stage)
            was_empty = root.exists()
            if was_empty:
                root.rmdir()
            try:
                stage.rename(root)
            except OSError:
                if was_empty and not root.exists():
                    root.mkdir(mode=0o700)
                raise
            fsync_directory(root.parent)
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description="Portable stopped-server state migration")
    parser.add_argument("action", choices=("backup","restore"))
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    opts = parser.parse_args(argv)
    try:
        if opts.action == "backup":
            backup(opts.data, opts.archive)
        else:
            restore(opts.archive, opts.data)
    except (StateBusy, OSError, ValueError, sqlite3.Error, BadZipFile, TypeError) as exc:
        parser.exit(1, f"{opts.action} failed: {exc}\n")
    print(f"{opts.action} complete.")


if __name__ == "__main__":
    main()

