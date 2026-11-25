"""Build a private, content-addressed YooAsset data package for the PC 1.1.12 plaza."""
from datetime import datetime, timedelta
import hashlib
import struct
import zlib

from realm_plaza import KOI, koi_deadline, controlled_activities, photo_deadline, photo_editions

STOCK_BUNDLE = "e49d461782ed9bf3309d2c35662e132a"
NOVEMBER_BUNDLE = "4479579c8fb33fb720ec2dadebe34778"
# TextAsset payload bounds verified against each digest-pinned serialized file.
SHEETS = {"BlessingTree":(11185388,19948), "Scene": (10000616, 21774), "Activity": (8130600, 19041), "SeasonalMiniGames": (7522068, 191),
          "ActivityJinli": (11632344,39), "NPC": (11760004,14188), "ActivityFashionShow": (719004,186)}
SHEETS_NOVEMBER = {"BlessingTree":(11186676,19948), "Scene":(10002432,21245), "Activity":(8132416,19041),
                   "SeasonalMiniGames":(7523836,191),"ActivityJinli":(11633632,39),"NPC":(11761292,14188),
                   "ActivityFashionShow":(719004,186)}
KNOWN_BUNDLES = {STOCK_BUNDLE:(SHEETS,b"39ae3d4a",0xb04965e4), NOVEMBER_BUNDLE:(SHEETS_NOVEMBER,b"af6e22c5",0xbad7850d)}
# The 11/25 table rescheduled rows 45–58. Each replacement refers to the identical prefab path.
NOVEMBER_THEME_ROWS={45:22,46:23,47:20,49:21,50:4,51:5,52:6,53:7,54:8,55:9,56:10,57:11,58:23}
OFF_START, OFF_END = "2000-01-01 00:00:00", "2000-01-01 00:00:01"


def _lz4(src, expected):
    data = bytearray()
    cursor = 0
    def length(n):
        nonlocal cursor
        if n == 15:
            while True:
                value = src[cursor]
                cursor += 1
                n += value
                if value < 255:
                    break
        return n
    while cursor < len(src):
        token = src[cursor]
        cursor += 1
        literal = length(token >> 4)
        data.extend(src[cursor:cursor+literal])
        cursor += literal
        if cursor == len(src):
            break
        stride = int.from_bytes(src[cursor:cursor+2], "little")
        cursor += 2
        count = length(token & 15) + 4
        if not 0 < stride <= len(data) or len(data) + count > expected:
            raise ValueError("Invalid LZ4 match in the stock package")
        chunk = data[-stride:] if stride <= count else data[len(data)-stride:len(data)-stride+count]
        data.extend((chunk * ((count+len(chunk)-1)//len(chunk)))[:count])
    if len(data) != expected:
        raise ValueError("Unexpected LZ4 block size")
    return data


def _varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        part = data[pos]
        pos += 1
        value |= (part & 127) << shift
        if part < 128:
            return value, pos
    raise ValueError("Invalid config varint")


def _fields(data, pos, end):
    while pos < end:
        tag, pos = _varint(data, pos)
        if tag & 7 == 2:
            size, pos = _varint(data, pos)
            yield tag >> 3, pos, pos+size
            pos += size
        elif tag & 7 == 0:
            value, pos = _varint(data, pos)
            yield tag >> 3, value, None
        else:
            raise ValueError("Unsupported field in the selected native config row")


def edit_dates(node, sheets, sheet, group, start_field, rows):
    offset, size = sheets[sheet]
    if struct.unpack_from("<I", node, offset-4)[0] != size:
        raise ValueError("Unexpected TextAsset length")
    for number, begin, end in _fields(node, offset, offset+size):
        if number != group:
            continue
        for _, a, b in _fields(node, begin, end):
            values = {field: (v, stop) for field, v, stop in _fields(node, a, b)}
            ident = values[1][0]
            if ident not in rows:
                continue
            for field, text in enumerate(rows[ident], start_field):
                i,j = values[field]
                if j-i != 19:
                    raise ValueError("Unexpected native date width")
                node[i:j] = text.encode("ascii")


def _stage_dates(node, config, sheets):
    interval = (config["opens_at"], config["closes_at"])
    theme = 25 if config["theme"] == 2 else config["theme"]  # first Halloween edition has no native dates.
    if sheets is SHEETS_NOVEMBER:
        theme=NOVEMBER_THEME_ROWS.get(theme,theme)
    if {260326150,260815150}.intersection(config["activities"]):
        edit_dates(node, sheets, "NPC", 1, 4, {10301: interval})
    if KOI in config["activities"]:
        edit_dates(node, sheets, "ActivityJinli", 1, 2, {KOI: (koi_deadline(*interval),)})
    selected_shows = set(config["activities"]).intersection(photo_editions())
    if selected_shows:
        edit_dates(node, sheets, "ActivityFashionShow", 1, 2,
                   {aid: (photo_deadline(aid,*interval),) for aid in selected_shows})
    edit_dates(node, sheets, "Scene", 4, 3, {i: interval if i == theme else (OFF_START, OFF_END) for i in range(3,59)})
    selected = set(config["activities"])
    controlled = controlled_activities(selected)
    edit_dates(node, sheets, "Activity", 1, 3, {i: interval if i in selected else (OFF_START, OFF_END) for i in controlled})
    edit_dates(node, sheets, "SeasonalMiniGames", 1, 2, {i: interval if i == config["arcade"] else (OFF_START, OFF_END)
                                                  for i in range(12000,12004)})


def updated_bundle(blob, config):
    layout = KNOWN_BUNDLES.get(hashlib.md5(blob).hexdigest())
    if layout is None:
        raise ValueError("Unexpected national config bundle")
    header = blob.index(b"\0",blob.index(b"\0",12)+1)+1
    size, packed, size_info, flags = struct.unpack_from(">QIII",blob,header)
    if size != len(blob) or flags != 0x243:
        raise ValueError("Unexpected UnityFS data layout")
    start_info = (header+20+15)&~15
    info = _lz4(blob[start_info:start_info+packed], size_info)
    n, = struct.unpack_from(">I", info,16)
    cursor = (start_info+packed+15)&~15
    chunks, node = [], bytearray()
    for i in range(n):
        size, length, mode = struct.unpack_from(">IIH",info,20+10*i)
        raw = blob[cursor:cursor+length]
        if mode not in (0,2,3):
            raise ValueError("Unexpected stock compression")
        node.extend(_lz4(raw,size) if mode else raw)
        chunks.append((size,raw))
        cursor += length
    if cursor != len(blob):
        raise ValueError("Invalid UnityFS data end")
    original = bytes(node)
    if "theme" in config:
        _stage_dates(node, config, layout[0])
    if config.get("blessings"):
        from realm_blessings import dates
        edit_dates(node,layout[0],"BlessingTree",1,2,{row["id"]:dates(row) if row["enabled"] else (OFF_START,OFF_END)
                    for row in config["blessings"]})
    blocks = []
    offset = 0
    for i,(size,raw) in enumerate(chunks):
        if original[offset:offset+size] != node[offset:offset+size]:
            raw = node[offset:offset+size]
            struct.pack_into(">IIH",info,20+10*i,size,size,0)
        blocks.append(raw)
        offset += size
    # Retain compressed payload blocks outside the edited TextAssets.
    start_data = (start_info+len(info)+15)&~15
    output = bytearray(blob[:start_info])
    struct.pack_into(">QIII", output, header, start_data+sum(map(len,blocks)), len(info), len(info), flags&~63)
    output.extend(info)
    output.extend(bytes(start_data-len(output)))
    output.extend(b"".join(blocks))
    return bytes(output), zlib.crc32(node)


def build(manifest, blob, config, patch, prefix):
    bundle, unity_crc = updated_bundle(blob, config)
    digest = hashlib.md5(bundle).hexdigest()
    # The release client uses date-shaped package versions. Keep the native format.
    key = hashlib.sha256(bundle).hexdigest()
    version = (datetime(2100,1,1)+timedelta(seconds=int(key[:9],16))).strftime("%Y-%m-%d-%H-%M-%S")
    name = f"{prefix}_{version}"
    base_digest=hashlib.md5(blob).hexdigest()
    _,crc,stock_unity_crc=KNOWN_BUNDLES[base_digest]
    old=base_digest.encode()
    if manifest.count(old) != 1 or manifest.count(patch.encode()) != 1:
        raise ValueError("Ambiguous configuration bundle in stock manifest")
    changed = bytearray(manifest.replace(patch.encode(),version.encode()))
    p = changed.index(old)
    if changed[p+32:p+42] != b"\x08\x00"+crc or struct.unpack_from("<I",changed,p-6)[0] != stock_unity_crc:
        raise ValueError("Unexpected stock YooAsset record")
    struct.pack_into("<I",changed,p-6,unity_crc)
    changed[p:p+32] = digest.encode()
    changed[p+34:p+42] = struct.pack("<I",zlib.crc32(bundle)).hex().encode()
    struct.pack_into("<Q",changed,p+42,len(bundle))
    return version, {name+".bytes":bytes(changed),name+".hash":hashlib.md5(changed).hexdigest().encode(),
                     digest+".bundle":bundle}
