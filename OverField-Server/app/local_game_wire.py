"""Bounded native of-ps TCP packet framing and Snappy raw block decoding."""
import struct

from proto.net_pb2 import PacketHead

MAX_BODY = 2 * 1024 * 1024
MAX_HEADER = 128



def read_exact(sock, size):
    output = bytearray(size)
    view = memoryview(output)
    while view:
        n = sock.recv_into(view)
        if not n:
            raise EOFError
        view = view[n:]
    return bytes(output)


def decode_snappy(src):
    # Snappy block-format preamble is an unsigned little-endian base-128 length.
    pos = 0
    size = 0
    for shift in (0, 7, 14, 21, 28):
        if pos >= len(src):
            raise ValueError("short snappy preamble")
        octet = src[pos]
        pos += 1
        size |= (octet & 127) << shift
        if not octet & 128:
            break
    else:
        raise ValueError("invalid snappy length")
    if size > MAX_BODY:
        raise ValueError("decoded message too large")
    out = bytearray()
    while pos < len(src):
        tag = src[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            n = tag >> 2
            if n >= 60:
                width = n - 59
                if pos + width > len(src):
                    raise ValueError("short literal length")
                n = int.from_bytes(src[pos:pos+width], "little")
                pos += width
            n += 1
            if pos + n > len(src) or len(out) + n > size:
                raise ValueError("invalid literal size")
            out.extend(src[pos:pos+n])
            pos += n
        else:
            width = (1, 2, 4)[kind-1]
            if pos + width > len(src):
                raise ValueError("short copy offset")
            offset = int.from_bytes(src[pos:pos+width],"little")
            pos += width
            if kind == 1:
                offset |= (tag & 224) << 3
                n = ((tag >> 2) & 7) + 4
            else:
                n = (tag >> 2) + 1
            if not 0 < offset <= len(out) or len(out) + n > size:
                raise ValueError("invalid snappy back reference")
            if n <= offset:
                out.extend(out[len(out)-offset:len(out)-offset+n])
            else:
                period = out[-offset:]
                out.extend((period * ((n+offset-1)//offset))[:n])
    if len(out) != size:
        raise ValueError("snappy size mismatch")
    return bytes(out)


def receive(sock):
    header_size = struct.unpack(">H",read_exact(sock,2))[0]
    if not 0 < header_size <= MAX_HEADER:
        raise ValueError("invalid header size")
    h=PacketHead.FromString(read_exact(sock,header_size))
    if not h.msg_id or h.body_len > MAX_BODY or h.flag not in (0,1) or h.total_pack_count>1:
        raise ValueError("unsupported frame")
    body=read_exact(sock,h.body_len)
    return h, decode_snappy(body) if h.flag else body


def broadcast_frames(msg_id, body):
    """Share immutable frames within one broadcast when receivers have the same sequence."""
    if len(body) > MAX_BODY:
        return None  # Session.send retains its normal validation order for oversized frames.
    header = PacketHead(msg_id=msg_id, body_len=len(body), packet_id=0)
    frames = {}

    def encode_for(sequence):
        frame = frames.get(sequence)
        if frame is None:
            header.seq_id = sequence
            serialized = header.SerializeToString()
            frame = struct.pack(">H", len(serialized)) + serialized + body
            frames[sequence] = frame
        return frame

    return encode_for


def encode(msg_id, body, packet_id=0, sequence=0):
    if len(body)>MAX_BODY:
        raise ValueError("response too large")
    h=PacketHead(msg_id=msg_id,body_len=len(body),packet_id=packet_id,seq_id=sequence).SerializeToString()
    return struct.pack(">H",len(h))+h+body

