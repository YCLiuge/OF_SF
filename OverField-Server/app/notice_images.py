"""Bounded PNG/JPEG assets stored with realm notices."""
import hashlib
from pathlib import Path
import re
import stat
import struct
import zlib

IMAGE_PATH = "/private/notice-images/"
MAX_IMAGE = 4 * 1024 * 1024
MAX_IMAGES = 4
HEX = re.compile(r"[a-f0-9]{64}")


def metadata(digest, mime, width, height, size):
    if not isinstance(digest, str) or not HEX.fullmatch(digest) or mime not in ("image/png", "image/jpeg"):
        raise ValueError("Invalid notice image")
    if any(type(n) is not int or not 1 <= n <= 4096 for n in (width, height)):
        raise ValueError("Notice image dimensions must be 1 to 4096 pixels")
    if type(size) is not int or not 1 <= size <= MAX_IMAGE:
        raise ValueError("Notice image exceeds 4 MiB")
    return dict(sha256=digest, url=IMAGE_PATH + digest, mime=mime, width=width, height=height, size=size)


def checked_images(items):
    if not isinstance(items, list) or len(items) > MAX_IMAGES:
        raise ValueError("Expected at most four notice images")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid notice image descriptor")
        desc = metadata(*(item.get(k) for k in ("sha256", "mime", "width", "height", "size")))
        if item.get("url") != desc["url"]:
            raise ValueError("Notice image must belong to this realm")
        result.append(desc)
    return result


def png_dimensions(data):
    cursor = 8
    dims = None
    has_data = False
    while cursor + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, cursor)
        end = cursor + 8 + size
        if end + 4 > len(data) or zlib.crc32(memoryview(data)[cursor + 4:end]) != int.from_bytes(data[end:end + 4], "big"):
            break
        if dims is None:
            if kind != b"IHDR" or size != 13:
                break
            dims = struct.unpack_from(">II", data, cursor + 8)
        elif kind == b"IHDR":
            break
        if kind == b"IDAT" and size:
            has_data = True
        if kind == b"IEND":
            if size == 0 and end + 4 == len(data) and has_data:
                return dims
            break
        cursor = end + 4
    raise ValueError("Invalid PNG notice image")


def jpeg_dimensions(data):
    cursor = 2
    dims = None
    while cursor + 4 <= len(data) and data[cursor] == 255:
        cursor += 1
        while cursor < len(data) and data[cursor] == 255:
            cursor += 1
        if cursor + 3 > len(data):
            break
        marker = data[cursor]
        size = int.from_bytes(data[cursor + 1:cursor + 3], "big")
        if size < 2 or cursor + size + 1 > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2):
            if size < 8 or dims:
                break
            height, width = struct.unpack_from(">HH", data, cursor + 4)
            dims = (width, height)
        if marker == 0xda:
            if dims and size >= 6 and data.endswith(b"\xff\xd9"):
                return dims
            break
        cursor += size + 1
    raise ValueError("Invalid JPEG notice image")


def inspect(data):
    if not 0 < len(data) <= MAX_IMAGE:
        raise ValueError("Notice image exceeds 4 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, dims = "image/png", png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        mime, dims = "image/jpeg", jpeg_dimensions(data)
    else:
        raise ValueError("Choose a PNG or JPEG notice image")
    return metadata(hashlib.sha256(data).hexdigest(), mime, *dims, len(data))


def publish_image(data):
    desc = inspect(data)
    if desc["mime"] == "image/jpeg":
        from notice_jpeg import encode
        data = encode(data, desc["width"], desc["height"])
        desc = inspect(data)
        if desc["mime"] != "image/png":
            raise ValueError("JPEG conversion did not produce a native PNG notice")
    return desc, data


def load_files(paths):
    paths = tuple(paths)
    if len(paths) > MAX_IMAGES:
        raise ValueError("Expected at most four notice images")
    loaded = []
    for item in paths:
        path = Path(item)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Choose a regular notice image file")
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE + 1)
        loaded.append(publish_image(data))
    return loaded


def read_response(response, digest):
    if response.status == 404:
        raise FileNotFoundError
    length = response.getheader("Content-Length", "")
    if response.status != 200 or not re.fullmatch(r"[0-9]{1,8}", length) or not 0 < int(length) <= MAX_IMAGE:
        raise ValueError("Invalid notice image response")
    data = response.read(int(length) + 1)
    desc = inspect(data)
    if len(data) != int(length) or desc["sha256"] != digest or desc["mime"] != response.getheader("Content-Type"):
        raise ValueError("Notice image integrity mismatch")
    return desc, data
