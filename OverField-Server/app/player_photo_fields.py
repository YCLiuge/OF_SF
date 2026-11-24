"""Only fields the player explicitly publishes in the photo wall may leave the local gateway."""
import struct

from photo_media import dimensions
from private_chat_text import chat_text
from proto import net_pb2 as pb


def public_image(data):
    dimensions(data)
    # Do not transmit EXIF, XMP or ICC profiles embedded by another image producer.
    chunks = []
    pos = 12
    while pos < len(data):
        name = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size
        if name not in (b"EXIF", b"XMP ", b"ICCP"):
            payload = data[pos + 8:end]
            if name == b"VP8X":
                payload = bytes([payload[0] & ~(0x20 | 0x08 | 0x04)]) + payload[1:]
            chunks.append(name + struct.pack("<I", size) + payload + b"\0" * (size & 1))
        pos = end + (size & 1)
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def text(value, units, guard):
    checked = chat_text(value)
    if (checked is None or len(checked.encode("utf-16-le")) > 2 * units or
            guard is not None and not guard.allows(checked)):
        raise ValueError("Invalid player photo wall text")
    return checked


def prepare(msg, clean, guard):
    if isinstance(msg, pb.PhotoShareSearchReq):
        query = text(msg.text,60,guard) if msg.search_type == pb.PhotoSearchType_SEARCH else ""
        clean(msg)
        msg.text = query
        return
    fields = {"title": text(msg.title,60,guard), "tag1": text(msg.tag1,24,guard), "tag2": text(msg.tag2,24,guard)}
    if not fields["title"]:
        raise ValueError("A published photo needs a title")
    if isinstance(msg, pb.UploadPhotoShareReq):
        fields.update(file=public_image(msg.file), tag3=text(msg.tag3,24,guard))
    clean(msg)
    for name,value in fields.items():
        setattr(msg,name,value)
