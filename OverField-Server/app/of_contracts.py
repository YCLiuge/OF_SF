"""Public, read-only discovery for OF_SF's optional content extensions."""
import hashlib
import json
import os
import re
from threading import Lock
from unicodedata import category


VERSION = "0.1.0"
REALM_PATH = "/of-contracts/v1/realm"
CONTENT_PATH = "/of-contracts/v1/content"
ROUTES = frozenset((REALM_PATH, CONTENT_PATH))
CONTENT_FILE = "of-contracts-content.json"
MAX_BYTES = 64 * 1024
MAX_ITEMS = 128
NAME = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def identifier(value):
    return isinstance(value, str) and 3 <= len(value) <= 96 and NAME.fullmatch(value) is not None


def display_text(value, maximum, multiline=False):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip():
        return False
    if not multiline and value != value.strip():
        return False
    return all(category(ch) not in ("Cc", "Cs") or (multiline and ch == "\n") for ch in value)


def validate(document):
    if not isinstance(document, dict) or document.keys() != {"contract_version", "items"}:
        raise ValueError("Invalid catalog fields")
    items = document["items"]
    if document["contract_version"] != VERSION or not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Invalid catalog version or size")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not identifier(item.get("id")) or not display_text(item.get("title"), 80):
            raise ValueError("Invalid catalog item")
        if item["id"] in seen:
            raise ValueError("Duplicate catalog id")
        seen.add(item["id"])
        if item.get("kind") == "minigame":
            if item.keys() != {"kind", "id", "title", "entry"} or not identifier(item["entry"]):
                raise ValueError("Invalid minigame entry")
        elif item.get("kind") == "guide":
            if item.keys() != {"kind", "id", "title", "text"} or not display_text(item["text"], 2048, multiline=True):
                raise ValueError("Invalid guide")
        else:
            raise ValueError("Unknown catalog kind")


def json_bytes(document):
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encode_catalog(raw):
    """Validate an operator document and return the exact UTF-8 entity sent to readers."""
    if len(raw) > MAX_BYTES:
        raise ValueError("Extension catalog exceeds 64 KiB")
    try:
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
    except RecursionError as exc:
        raise ValueError("Extension catalog is nested too deeply") from exc
    validate(document)
    return json_bytes(document)


def responses(content):
    realm = {"contract_id": "ofsf.extensions.v1", "contract_version": VERSION,
             "baseline": "ofsf.native", "client_profile": "ofsf.pc.native.v1",
             "content_manifest": hashlib.sha256(content).hexdigest(),
             "ruleset_revision": None, "capabilities": ["content.catalog.v1"]}
    return {REALM_PATH: json_bytes(realm), CONTENT_PATH: content}


EMPTY = responses(json_bytes({"contract_version": VERSION, "items": []}))


def signature(stat):
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class ContentCatalog:
    def __init__(self, state):
        self.path = state / CONTENT_FILE
        self.lock = Lock()
        self.key = None
        self.pages = EMPTY

    def read(self, route):
        # Operators publish with atomic replace. Pin the bytes and hash to the same open file.
        with self.lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                self.key, self.pages = None, EMPTY
                return EMPTY[route]
            if self.key != signature(stat):
                with self.path.open("rb") as stream:
                    key = signature(os.fstat(stream.fileno()))
                    raw = stream.read(MAX_BYTES + 1)
                try:
                    pages = responses(encode_catalog(raw))
                except ValueError:
                    pages = None
                self.key, self.pages = key, pages
            if self.pages is None:
                raise ValueError("Invalid extension catalog")
            return self.pages[route]
