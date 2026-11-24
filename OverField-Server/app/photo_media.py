"""Bounded original Unity WebP uploads and the photo wall's local/forwarded media routes."""
from contextlib import closing
import http.client
import re
import sqlite3
import struct


MAX_IMAGE = 2 * 1024 * 1024
PUBLIC = "/private/photos/"
NATIVE = "/private-game/photos/"
PHOTO_PATH = re.compile(r"(?:thumb/)?([1-9][0-9]{0,18})\Z")


def dimensions(data):
    if not 20 < len(data) <= MAX_IMAGE or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Expected a WebP photo")
    if int.from_bytes(data[4:8], "little") != len(data) - 8:
        raise ValueError("Invalid WebP size")
    position, shape, canvas = 12, None, None
    while position + 8 <= len(data):
        kind, size = struct.unpack_from("<4sI", data, position)
        start = position + 8
        end = start + size
        position = end + (size & 1)
        if position > len(data):
            raise ValueError("Truncated photo")
        if kind == b"VP8X":
            if size != 10 or data[start] & 2 or canvas:
                raise ValueError("Invalid photo canvas")
            canvas = tuple(1 + int.from_bytes(data[start+i:start+i+3],"little") for i in (4,7))
        elif kind == b"VP8 ":
            if shape or size <= 10 or data[start] & 1 or data[start+3:start+6] != b"\x9d\x01\x2a":
                raise ValueError("Invalid WebP frame")
            if int.from_bytes(data[start:start+3],"little") >> 5 > size - 10:
                raise ValueError("Truncated WebP partition")
            shape = tuple(n & 0x3fff for n in struct.unpack_from("<HH",data,start+6))
        elif kind == b"VP8L":
            if shape or size < 6 or data[start] != 0x2f:
                raise ValueError("Invalid lossless WebP")
            bits = int.from_bytes(data[start+1:start+5], "little")
            if bits >> 29:
                raise ValueError("Unknown lossless WebP version")
            shape = (1 + (bits & 16383), 1 + ((bits >> 14) & 16383))
        elif kind not in (b"ALPH", b"ICCP", b"XMP ", b"EXIF"):
            raise ValueError("Unexpected photo chunk")
    if (position != len(data) or not shape or canvas and canvas != shape
            or any(not 1 <= n <= 4096 for n in shape)):
        raise ValueError("Invalid photo dimensions")
    return shape


def local_photo(root, code, *, head=False):
    path = (root / "game.sqlite").resolve()
    if not path.is_file():
        raise FileNotFoundError
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='realm_photo_media'").fetchone():
            raise FileNotFoundError
        content = "length(m.file)" if head else "m.file"
        row = conn.execute(f"""SELECT {content} FROM realm_photo_media m JOIN realm_photos p ON p.code=m.code
                             WHERE p.code=?""", (code,)).fetchone()
    if row is None:
        raise FileNotFoundError
    return row[0]


def moderate(root, operation, data):
    """The owner-only control plane lists metadata or atomically withdraws a published image."""
    path = (root / "game.sqlite").resolve()
    listing = operation == "photos.list"
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + ("?mode=ro" if listing else "?mode=rw"),
                                     uri=True, timeout=3)) as conn, conn:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE name='realm_photos'").fetchone()
            if present and listing:
                rows = conn.execute("SELECT code,name,title,activity,posted FROM realm_photos WHERE code < ?"
                                    " ORDER BY code DESC LIMIT ?", (data.get("before",2**63-1),data.get("limit",30)))
                return {"photos":[dict(code=c,author=n,title=t,activity=a,posted_ms=ts) for c,n,t,a,ts in rows]}
            if present:
                conn.execute("PRAGMA foreign_keys=ON")
                if conn.execute("DELETE FROM realm_photos WHERE code=?", (data["code"],)).rowcount:
                    return {"withdrawn":data["code"]}
    if listing:
        return {"photos":[]}
    raise FileNotFoundError("Published photo not found")


def forwarded_photo(remote, code, *, head=False):
    with remote.channels.open(remote.http_port) as channel:
        conn = http.client.HTTPConnection("127.0.0.1",remote.http_port,timeout=14)
        conn.sock = channel
        try:
            conn.request("HEAD" if head else "GET", PUBLIC + str(code), headers={"Connection":"close"})
            response = conn.getresponse()
            if response.status == 404:
                raise FileNotFoundError
            if response.status != 200 or response.getheader("Content-Type") != "image/webp":
                raise ValueError("Photo unavailable")
            size = int(response.getheader("Content-Length", "-1"))
            if not 0 < size <= MAX_IMAGE:
                raise ValueError("Invalid remote photo size")
            if head:
                return size
            raw = response.read(size + 1)
            if len(raw) != size:
                raise ValueError("Truncated remote photo")
            dimensions(raw)
            return raw
        finally:
            conn.close()


def serve(handler):
    path = handler.path
    native = path.startswith(NATIVE)
    if not native and not path.startswith(PUBLIC):
        return False
    if any(k in handler.headers for k in ("Authorization","Cookie","Proxy-Authorization")):
        handler._reply(403, {"error":"public photo request does not accept credentials"})
        return True
    try:
        match = PHOTO_PATH.fullmatch(path[len(NATIVE if native else PUBLIC):])
        if not match or int(match[1]) > 2**63-1:
            raise FileNotFoundError
        source = getattr(getattr(handler.server,"game_dispatch",None),"maintenance",None) if native else handler.server.maintenance
        if source is None:
            raise FileNotFoundError
        head = handler.command == "HEAD"
        raw = source.open_photo(int(match[1]),head=head)
        handler.send_response(200)
        handler.send_header("Content-Type","image/webp")
        handler.send_header("Content-Length",str(raw if head else len(raw)))
        handler.send_header("X-Content-Type-Options","nosniff")
        handler.send_header("Cache-Control","no-store")
        handler.end_headers()
        if not head:
            handler.wfile.write(raw)
    except FileNotFoundError:
        handler._reply(404, {"error":"photo not found"})
    except (OSError,ValueError,sqlite3.Error,http.client.HTTPException):
        handler._reply(503, {"error":"photo unavailable"})
    return True
