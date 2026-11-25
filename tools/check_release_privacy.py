"""Look for a privately supplied realm's actual address and CA in release archives or a Git checkout."""
import argparse
import base64
import bz2
import gzip
import io
import json
from pathlib import Path
import ssl
import subprocess
import tarfile
from urllib.parse import urlsplit
import zipfile


def private_terms(invitation):
    data = json.loads(invitation.read_text(encoding="utf-8-sig"))
    address = data["endpoint"]
    host = urlsplit(address).hostname
    if not host:
        raise ValueError("Invalid connection invitation")
    terms = set()
    for text in {address, host, host.upper()}:
        for encoding in ("utf-8", "utf-16le", "utf-16be"):
            terms.add(text.encode(encoding))
    if data.get("caPem"):
        der = ssl.PEM_cert_to_DER_cert(data["caPem"])
        terms.add(der)
        # PEM variants wrap at different widths; the certificate signature tail is issuer-specific.
        tail = base64.b64encode(der)[-70:-30]
        terms.update([tail, tail.decode("ascii").encode("utf-16le")])
        for line in data["caPem"].splitlines()[-3:-2]:
            if len(line) >= 30:
                terms.add(line.strip().encode("ascii"))
    return terms


class Audit:
    def __init__(self, terms):
        self.terms = terms
        self.files = 0
        self.archives = 0

    def raw(self, data, label):
        self.files += 1
        if any(term in data for term in self.terms):
            # Labels may themselves contain private information. Report the location only if safe.
            shown = label if not any(term in label.encode("utf-8") for term in self.terms) else "[private filename]"
            raise ValueError("Private connection material found in " + shown)

    def member(self, data, label, depth=0):
        if depth > 8 or len(data) > 256 * 1024 * 1024:
            raise ValueError("Release audit exceeded supported member size or nesting depth")
        self.raw(data, label)
        file = io.BytesIO(data)
        # Python's own zipfile bytecode contains EOCD literal strings; only actual ZIP
        # headers or a self-extracting executable can be nested distribution ZIPs.
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"MZ")) and zipfile.is_zipfile(file):
            self.archives += 1
            with zipfile.ZipFile(file) as archive:
                for item in archive.infolist():
                    self.raw(item.filename.encode("utf-8"), "archive member name")
                    if item.file_size > 256 * 1024 * 1024:
                        raise ValueError("Unexpected large release member")
                    self.member(archive.read(item), label + "/" + item.filename, depth + 1)
        elif data.startswith(b"\x1f\x8b"):
            self.archives += 1
            if label.lower().endswith((".tar.gz", ".tgz")):
                with tarfile.open(fileobj=io.BytesIO(data),mode="r:gz") as archive:
                    for item in archive:
                        self.raw(item.name.encode("utf-8"), "archive member name")
                        if item.isfile():
                            if item.size > 256 * 1024 * 1024:
                                raise ValueError("Unexpected large release member")
                            self.member(archive.extractfile(item).read(), label + "/" + item.name, depth + 1)
            else:
                self.member(gzip.decompress(data), label + "/[gzip]", depth + 1)
        elif data.startswith(b"BZh"):
            self.archives += 1
            self.member(bz2.decompress(data), label + "/[bzip2]", depth + 1)

    def git(self, root):
        cmd = ["git", "-C", str(root)]
        files = subprocess.check_output(cmd + ["ls-files", "-z"]).split(b"\0")
        for rel in filter(None, files):
            self.raw(rel, "working tree name")
            path = root / rel.decode("utf-8")
            if path.is_file():
                self.member(path.read_bytes(), "working tree/" + rel.decode("utf-8"))
        refs = subprocess.check_output(cmd + ["rev-list", "--objects", "--all", "--no-object-names"]).splitlines()
        if refs:
            # Process content and metadata locally. No blob or commit text is printed.
            with subprocess.Popen(cmd + ["cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as batch:
                for ref in refs:
                    batch.stdin.write(ref+b"\n")
                    batch.stdin.flush()
                    oid, kind, size = batch.stdout.readline().split()
                    data = batch.stdout.read(int(size))
                    if batch.stdout.read(1) != b"\n":
                        raise ValueError("Corrupt Git object stream")
                    label = "Git object " + oid.decode("ascii")[:12]
                    if kind == b"blob":
                        self.member(data, label)
                    else:
                        self.raw(data, label)
                batch.stdin.close()
        return len(files)-1, len(refs)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--invitation", type=Path, required=True, help="Private local file; never copied")
    cli.add_argument("--archive", action="append", type=Path, default=[])
    cli.add_argument("--git", type=Path)
    args = cli.parse_args()
    if not args.archive and not args.git:
        cli.error("Choose at least one --archive or --git")
    audit = Audit(private_terms(args.invitation))
    for artifact in args.archive:
        audit.member(artifact.read_bytes(), artifact.name)
    if args.git:
        tracked, objects = audit.git(args.git)
        print(f"Git checked: {tracked} working-tree paths and {objects} reachable objects")
    print(f"PASS release privacy scan: {audit.files} content/name checks, {audit.archives} expanded containers.")
