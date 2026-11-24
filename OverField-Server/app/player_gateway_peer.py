"""Authenticate a per-source rate-limit bucket on this realm's loopback HTTP hop."""
from contextlib import closing
import hashlib
import hmac
import ipaddress
from pathlib import Path
import re
import sqlite3
import time


HEADER = "X-OF-Gateway-Rate"
FORMAT = re.compile(r"([0-9]{1,12})\.([a-f0-9]{64})\.([a-f0-9]{64})\Z")


def prepare(identity):
    with identity._write() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS realm_gateway_key(
            id INTEGER PRIMARY KEY CHECK(id=1), key BLOB NOT NULL CHECK(length(key)=32))""")
        db.execute("INSERT OR IGNORE INTO realm_gateway_key VALUES(1,randomblob(32))")
        return db.execute("SELECT key FROM realm_gateway_key WHERE id=1").fetchone()[0]


def load(state):
    path=(Path(state)/"identity.sqlite3").resolve()
    try:
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=5)) as db:
            row=db.execute("SELECT key FROM realm_gateway_key WHERE id=1").fetchone()
            if row and len(row[0])==32:
                return row[0]
    except sqlite3.Error:
        pass
    raise ValueError("Start the realm with the same --state before starting its player gateway")


def signature(key, path, body, stamp, source):
    data="\n".join(("OF-SF-account-rate/1","POST",path,hashlib.sha256(body).hexdigest(),stamp,source))
    return hmac.digest(key,data.encode("ascii"),"sha256").hex()


def sign(key, address, path, body):
    address=ipaddress.ip_address(address)
    if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped:
        address=address.ipv4_mapped
    source=hmac.digest(key,b"limit-source\0"+address.packed,"sha256").hex()
    stamp=str(int(time.time()))
    return ".".join((stamp,source,signature(key,path,body,stamp,source)))


def verify(key, header, path, body, peer, bind):
    if not ipaddress.ip_address(peer).is_loopback or not ipaddress.ip_address(bind).is_loopback:
        return None
    match=FORMAT.fullmatch(header)
    if not match:
        return None
    stamp,source,sig=match.groups()
    if abs(time.time()-int(stamp))>30 or not hmac.compare_digest(sig,signature(key,path,body,stamp,source)):
        return None
    return source
