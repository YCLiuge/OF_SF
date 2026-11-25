"""Typed name and numeric lookups for a private realm's own characters."""
import hashlib
import re

from local_identity import _name

NAME_KEY = re.compile(r"~ofn1-[a-f0-9]{64}\Z", re.ASCII)


def name_digest(value):
    try:
        _, normalized = _name(value)
    except ValueError:
        return b""
    return hashlib.sha256(("of-private-name-v1\0" + normalized).encode("utf-8")).digest()


def name_key(value):
    digest=name_digest(value)
    return "~ofn1-"+digest.hex() if digest else ""


def search_key(value):
    if not isinstance(value,str) or len(value)>64:
        return ""
    value=value.strip()
    if value.isascii() and value.isdigit():
        return str(int(value)) if len(value)<=10 and 0<int(value)<=0xffffffff else ""
    return name_key(value)
