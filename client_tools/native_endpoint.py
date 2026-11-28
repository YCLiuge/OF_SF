"""Export the retail 1.1.12 Unity discovery address into a dedicated client copy."""
import argparse
import base64
import bz2
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
KNOWN_SOURCE = "3583d81eedcc588f46efbf46ff22f48de9c80dd665c6ec028c0ee2d3b8f10a70"
TEMPLATE_FILE = HERE / "native_endpoint_template.json.bz2"
TEMPLATES_FILE = HERE / "native_endpoint_templates.json.bz2"
BLOCK = struct.Struct(">IIH")
ROW = struct.Struct("<qqIi")


def templates():
    path = TEMPLATES_FILE if TEMPLATES_FILE.is_file() else TEMPLATE_FILE
    value = json.loads(bz2.decompress(path.read_bytes()))
    if isinstance(value, dict) and isinstance(value.get("templates"), list):
        value = value["templates"]
    elif isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError("客户端配置模板无效。")
    return value


def template_for(source):
    digest = hashlib.file_digest(Path(source).open("rb"), "sha256").hexdigest()
    for template in templates():
        if template.get("source_sha256") == digest:
            return template
    return None


def is_known_source(source):
    try:
        return template_for(source) is not None
    except OSError:
        return False


def validate_endpoint(value):
    """Return an absolute HTTP(S) origin suitable for GameDispatch."""
    if not isinstance(value, str):
        raise ValueError("请输入区服地址。")
    value = value.strip()
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in "\\?#@%"):
        raise ValueError("请输入有效的 HTTP 或 HTTPS 区服地址。")
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.path not in ("", "/"):
        raise ValueError("区服地址示例为 http://192.168.1.8:18881，请仅填写协议、主机和端口。")
    if url.netloc.endswith(":") or url.port == 0:
        raise ValueError("区服端口必须在 1 至 65535 范围内。")
    host = url.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        host = host.encode("idna").decode("ascii").lower()
        absolute = host.endswith(".")
        labels = host.rstrip(".").split(".")
        if (not labels or len(host.rstrip(".")) > 253 or
                any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels) or
                all(part.isdigit() for part in labels) or host.endswith("..")):
            raise ValueError("主机名或 IP 地址无效。")
        host = ".".join(labels) + ("." if absolute else "")
    else:
        host = f"[{ip.compressed}]" if ip.version == 6 else str(ip)
    # urlsplit accepts trailing text following IPv6 brackets. Only a port may follow.
    if "[" in url.netloc or "]" in url.netloc:
        if not re.fullmatch(r"\[[^][]+\](?::[0-9]+)?", url.netloc):
            raise ValueError("IPv6 地址应置于方括号内。")
    suffix = "" if url.port is None else f":{url.port}"
    return url.scheme + "://" + host + suffix


def _align(size, boundary):
    return (size + boundary - 1) // boundary * boundary


def _lz4_block(raw):
    """Encode a UnityFS LZ4 block and retain the five-literal end marker."""
    raw = bytes(raw)
    size = len(raw)
    data = bytearray()
    seen = {}

    def extension(length):
        runs, tail = divmod(length,255)
        data.extend(bytes([255]) * runs)
        data.append(tail)

    def literal_run(anchor, end, match_length=0):
        literals = end - anchor
        data.append((min(literals,15) << 4) | min(match_length,15))
        if literals >= 15:
            extension(literals - 15)
        data.extend(raw[anchor:end])

    anchor = cursor = 0
    while cursor <= size - 12:
        key = raw[cursor:cursor+4]
        previous = seen.get(key)
        seen[key] = cursor
        if previous is None or cursor - previous > 65535:
            cursor += 1
            continue
        end = cursor + 4
        pos = previous + 4
        while end < size - 5 and raw[pos] == raw[end]:
            end += 1
            pos += 1
        matched = end - cursor - 4
        literal_run(anchor,cursor,matched)
        data.extend(struct.pack("<H",cursor - previous))
        if matched >= 15:
            extension(matched - 15)
        for point in range(max(cursor+1,end-2),end):
            seen[raw[point:point+4]] = point
        anchor = cursor = end
    literal_run(anchor,size)
    return bytes(data)


def _compress_block(raw, flags):
    if flags & 63 in (2,3):
        packed = _lz4_block(raw)
        if len(packed) < len(raw):
            return packed, flags
    return bytes(raw), flags & ~63


def _emit_blocks(template, url):
    meta = bytearray(base64.b64decode(template["metadata"]))
    count, = struct.unpack_from(">I",meta,16)
    table = [BLOCK.unpack_from(meta,20+i*BLOCK.size) for i in range(count)]
    raw = {int(i):bytearray(base64.b64decode(v)) for i,v in template["raw_blocks"].items()}
    starts = []
    cursor = 0
    for n, _, _ in table:
        starts.append(cursor)
        cursor += n

    def region(start, size, new=None):
        """Read or replace fixed-size metadata, including rows spanning block boundaries."""
        pieces = bytearray()
        for i, buf in raw.items():
            left, right = max(start,starts[i]), min(start+size,starts[i]+len(buf))
            if left < right:
                a, b = left-starts[i], right-starts[i]
                pieces.extend(buf[a:b])
                if new is not None:
                    buf[a:b] = new[left-start:right-start]
        if len(pieces) != size:
            raise ValueError("客户端配置模板缺少元数据。")
        return bytes(pieces)

    company_at = template.get("company_at",-1)
    if company_at is not None and company_at >= 0:
        expected_company = template.get("company","Inutan").encode("ascii")
        if region(company_at,6,b"OFPriv") != expected_company:
            raise ValueError("独立副本的 Unity 公司名称与原始模板不一致。")
    record = base64.b64decode(template["object_bytes"])
    old_url = template["original"].encode("ascii")
    new_url = url.encode("ascii")
    field = template["field_at"]
    old_size, new_size = _align(len(old_url),4), _align(len(new_url),4)
    if record[field:field+4+len(old_url)] != struct.pack("<I",len(old_url)) + old_url:
        raise ValueError("配置模板中的正式区服字段无效。")
    updated = (record[:field] + struct.pack("<I",len(new_url))
               + new_url.ljust(old_size if new_size <= old_size else new_size, b"\0")
               + record[field+4+old_size:])
    # Preserve Unity's uniform 128 KiB decompressed offsets. Short addresses retain padding.
    delta = _align(max(0,len(updated)-len(record)),131072)
    resource = template["file_base"]
    obj = template["config_file_offset"]
    relative = obj - template["serialized_data_offset"]
    if not template.get("skip_table"):
        region(resource+24,8,struct.pack(">Q",template["file_len"]+delta))
        for i in range(template["records"]):
            at = template["table_at"] + i*ROW.size
            pid, pos, length, kind = ROW.unpack(region(at,ROW.size))
            if pid == 2813:
                if pos != relative or length != len(record):
                    raise ValueError("配置对象与模板不一致。")
                region(at,ROW.size,ROW.pack(pid,pos,len(updated),kind))
            elif delta and pos > relative:
                region(at,ROW.size,ROW.pack(pid,pos+delta,length,kind))
    absolute = resource + obj
    i = next(i for i in raw if starts[i] <= absolute < starts[i]+len(raw[i]))
    offset = absolute - starts[i]
    if raw[i][offset:offset+len(record)] != record:
        raise ValueError("配置对象数据与模板不一致。")
    raw[i][offset:offset+len(record)] = updated.ljust(len(record)+delta,b"\0")
    cdn_at = template.get("cdn_at",-1)
    if cdn_at is not None and cdn_at >= 0:
        cdn_old = template["cdn_original"].encode("ascii")
        cdn_new = (url.rstrip("/") + "/Resources").encode("ascii")
        if len(cdn_new) > len(cdn_old):
            raise ValueError("区服地址过长，无法写入客户端 CDN 字段。")
        absolute = cdn_at
        ci = next(i for i in raw if starts[i] <= absolute < starts[i]+len(raw[i]))
        co = absolute - starts[i]
        if (raw[ci][co-4:co] != struct.pack("<I",len(cdn_old))
                or raw[ci][co:co+len(cdn_old)] != cdn_old):
            raise ValueError("配置模板中的 CDN 字段无效。")
        raw[ci][co-4:co] = struct.pack("<I",len(cdn_new))
        raw[ci][co:co+len(cdn_old)] = cdn_new.ljust(len(cdn_old),b"\0")
    if not template.get("skip_table"):
        struct.pack_into(">Q",meta,template["file_record"]+8,template["file_len"]+delta)
        for offset in template["other_nodes"]:
            start, = struct.unpack_from(">Q",meta,offset)
            struct.pack_into(">Q",meta,offset,start+delta)

    rows = []
    payload = {}
    for i, (n, packed, flags) in enumerate(table):
        if i not in raw:
            rows.append(BLOCK.pack(n,packed,flags))
            continue
        chunks = []
        for start in range(0,len(raw[i]),131072):
            part = raw[i][start:start+131072]
            data, block_flags = _compress_block(part,flags)
            rows.append(BLOCK.pack(len(part),len(data),block_flags))
            chunks.append(data)
        payload[i]=b"".join(chunks)
    new_meta = meta[:16] + struct.pack(">I",len(rows)) + b"".join(rows) + meta[20+count*BLOCK.size:]
    return table, payload, bytes(new_meta)


@contextmanager
def _target_lock(target):
    """Serialize publications of the same copy when multiple launchers are open."""
    with target.with_name("."+target.name+".endpoint.lock").open("a+b") as handle:
        if os.name=="nt":
            import msvcrt
            handle.seek(0,2)
            if handle.tell()==0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:
                raise ValueError("另一启动器正在修改同一个私服副本。") from exc
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def export_config(source, destination, endpoint, *, previous_sha256=None):
    """Create/atomically refresh an independent copy from the immutable known retail file."""
    url = validate_endpoint(endpoint)
    source = Path(source).resolve(strict=True)
    original_root = source.parent.parent if source.parent.name.lower()=="launcher_data" else source.parent
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("输出文件不能是符号链接。")
    destination = destination.resolve()
    if destination == source or destination.is_relative_to(original_root):
        raise ValueError("请使用官服目录之外的独立客户端副本。")
    template = template_for(source)
    if template is None:
        raise ValueError("原版文件与支持的国服 PC 1.1.12 版本不一致。")
    if source.stat().st_size != template["source_bytes"]:
        raise ValueError("原版文件与支持的国服 PC 1.1.12 版本不一致。")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with _target_lock(destination):
        if destination.exists():
            if not previous_sha256:
                raise FileExistsError("目标文件已存在。重新设置地址需要先验证之前的副本。")
            with destination.open("rb") as prior:
                if hashlib.file_digest(prior,"sha256").hexdigest()!=previous_sha256:
                    raise ValueError("现有私服副本已改变，保留现有文件。")
        table, replacement, metadata = _emit_blocks(template,url)
        tmp = None
        try:
            incoming, outgoing = hashlib.sha256(), hashlib.sha256()
            with source.open("rb") as src, tempfile.NamedTemporaryFile("wb",dir=destination.parent,
                        prefix=".of-address-",delete=False) as dst:
                tmp=Path(dst.name)
                def write(data):
                    dst.write(data)
                    outgoing.update(data)
                header=src.read(template["data_start"])
                incoming.update(header)
                at=template["header_at"]
                flags=struct.unpack_from(">I",header,at+16)[0]
                packed, flags = _compress_block(metadata,flags)
                first = _align(_align(at+20,16)+len(packed),16)
                length=first+sum(len(replacement[i]) if i in replacement else p for i,(_,p,_) in enumerate(table))
                prefix = header[:at]+struct.pack(">QIII",length,len(packed),len(metadata),flags)
                write((prefix.ljust(_align(len(prefix),16),b"\0")+packed).ljust(first,b"\0"))
                for i,(_,p,_) in enumerate(table):
                    original = src.read(p)
                    incoming.update(original)
                    write(replacement.get(i,original))
                if incoming.hexdigest()!=template["source_sha256"] or src.read(1):
                    raise ValueError("源配置的 SHA-256 与国服 1.1.12 不一致。")
                if dst.tell()!=length:
                    raise ValueError("新配置校验失败。")
                dst.flush()
                os.fsync(dst.fileno())
            if destination.exists():
                if not previous_sha256:
                    raise FileExistsError("输出文件已存在。")
                os.replace(tmp,destination)
            else:
                # Never replace an unexpected file created by another application.
                if os.name == "nt":
                    os.rename(tmp,destination)
                else:
                    os.link(tmp,destination)
            return {"url":url,"sha256":outgoing.hexdigest(),"bytes":length}
        finally:
            if tmp is not None:tmp.unlink(missing_ok=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description="导出原版 Unity 客户端的独立区服地址配置。")
    parser.add_argument("source",type=Path)
    parser.add_argument("destination",type=Path)
    parser.add_argument("endpoint")
    parser.add_argument("--previous-sha256")
    args=parser.parse_args()
    try:
        result=export_config(args.source,args.destination,args.endpoint,previous_sha256=args.previous_sha256)
    except (OSError, ValueError) as exc:
        parser.exit(1,str(exc)+"\n")
    print(json.dumps(result,ensure_ascii=False))

