"""TLS entry point for ordinary players connecting to this realm's two loopback services."""
import asyncio
from collections import deque
from contextlib import suppress
import ipaddress
from pathlib import Path
import re
import ssl
import struct
from time import monotonic

from google.protobuf.message import DecodeError
from local_game_wire import encode
from local_identity import TICKET
from photo_media import PHOTO_PATH, PUBLIC as PHOTO_PREFIX
import player_gateway_peer
from network.msg_id import MsgId
from proto import net_pb2 as pb


GET = {"/local/v1/region", "/private/plaza", "/private/gifts", "/private/maintenance"}
IMPORT_PREVIEW = "/local/v1/accounts/import-preview"
POST = {"/local/v1/accounts/"+action for action in ("challenge","lookup","register","login","bind","launch","claim-gift",
                                                   "import-preview", "import-apply", "import-undo")}
ATTACHMENT = re.compile(r"/private/(?:releases|notice-images)/[0-9a-f]{64}\Z")


class Gate:
    def __init__(self, http_port, game_port, limit, peer_key):
        self.peer_key = peer_key
        self.http_port = http_port
        self.game_port = game_port
        self.limit = limit
        self.connected = 0
        self.attempts = {}
        self.preview_uploads = 0

    def admit_account(self, address):
        now = monotonic()
        if address not in self.attempts and len(self.attempts) >= 4096:
            self.attempts = {key:q for key,q in self.attempts.items() if q and q[-1] > now-60}
            if len(self.attempts) >= 4096:
                return False
        times = self.attempts.setdefault(address, deque())
        while times and times[0] <= now-60:
            times.popleft()
        limit=600 if address in player_gateway_peer.PROXY_LOOPBACKS else 60
        if len(times) >= limit:
            return False
        times.append(now)
        return True

    async def account(self, reader, writer, address):
        # Rebuild exactly one allowlisted request. Nothing after its fixed-length body is relayed.
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        if len(request) > 8192:
            raise ValueError()
        lines = request[:-4].decode("ascii").split("\r\n")
        method, path, version = lines.pop(0).split(" ")
        if version not in ("HTTP/1.0", "HTTP/1.1"):
            raise ValueError()
        asset = ATTACHMENT.fullmatch(path) or path.startswith(PHOTO_PREFIX) and PHOTO_PATH.fullmatch(path[len(PHOTO_PREFIX):])
        if not ((method == "GET" and path in GET) or (method in ("GET","HEAD") and asset)
                or (method == "POST" and path in POST)):
            return await reject(writer, 404)
        headers = {}
        for line in lines:
            key, sep, value = line.partition(":")
            if not sep or not re.fullmatch(r"[A-Za-z0-9-]+", key) or key.lower() in headers:
                raise ValueError()
            headers[key.lower()] = value.strip()
        if headers.keys() & {"transfer-encoding", "upgrade", "expect", "authorization", "proxy-authorization", "cookie"}:
            raise ValueError()
        length = headers.get("content-length", "0")
        preview = method == "POST" and path == IMPORT_PREVIEW
        maximum = 2 * 1024 * 1024 if preview else 2048
        if not re.fullmatch(r"[0-9]{1,7}", length) or int(length) > maximum:
            return await reject(writer, 413)
        if method in ("GET","HEAD") and int(length) or method == "POST" and (
                not int(length) or headers.get("content-type", "").partition(";")[0].lower() != "application/json"):
            raise ValueError()
        if method == "POST" and not self.admit_account(address):
            return await reject(writer, 429)
        if preview and self.preview_uploads >= 4:
            return await reject(writer, 429)
        if preview:
            self.preview_uploads += 1
        try:
            body = await asyncio.wait_for(reader.readexactly(int(length)), 30 if preview else 8)
            await self.forward_account(writer,address,method,path,body)
        finally:
            if preview:
                self.preview_uploads -= 1

    async def forward_account(self, writer, address, method, path, body):
        backend, upstream = await loopback(self.http_port)
        try:
            head = (f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{self.http_port}\r\n"
                    "Connection: close\r\n")
            if method == "POST":
                attestation = player_gateway_peer.sign(self.peer_key, address, path, body)
                head += f"{player_gateway_peer.HEADER}: {attestation}\r\n"
                head += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            upstream.write((head+"\r\n").encode("ascii"))
            upstream.write(body)
            await upstream.drain()
            await transfer(backend, writer, 45)
        finally:
            await close(upstream)

    async def game(self, reader, writer):
        # This transport accepts only the fresh private ticket from the player's local gateway.
        # Forward a new frame with just that ticket, never an SDK identity or device identifiers.
        size, = struct.unpack(">H", await asyncio.wait_for(reader.readexactly(2), 7))
        if not 0 < size <= 128:
            raise ValueError()
        header = pb.PacketHead.FromString(await asyncio.wait_for(reader.readexactly(size), 7))
        if (header.msg_id != MsgId.VerifyLoginTokenReq or not 0 < header.body_len <= 256
                or header.flag or header.total_pack_count > 1):
            raise ValueError()
        message = pb.VerifyLoginTokenReq.FromString(await asyncio.wait_for(reader.readexactly(header.body_len), 7))
        if not TICKET.fullmatch(message.login_token):
            raise ValueError()
        backend, upstream = await loopback(self.game_port)
        try:
            fresh = pb.VerifyLoginTokenReq(login_token=message.login_token)
            upstream.write(encode(header.msg_id, fresh.SerializeToString(), header.packet_id))
            await upstream.drain()
            forward = asyncio.create_task(transfer(reader, upstream, 150))
            backward = asyncio.create_task(transfer(backend, writer, 150))
            try:
                await asyncio.wait((forward, backward), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for stream in (forward, backward):
                    stream.cancel()
                await asyncio.gather(forward, backward, return_exceptions=True)
        finally:
            await close(upstream)

    async def accept(self, reader, writer):
        if self.connected >= self.limit:
            await close(writer)
            return
        self.connected += 1
        try:
            route = await asyncio.wait_for(reader.readuntil(b"\n"), 7)
            if route == b"OF-SF/1 ACCOUNT\n":
                try:
                    await self.account(reader, writer, writer.get_extra_info("peername")[0])
                except (ValueError, UnicodeError, asyncio.LimitOverrunError):
                    await reject(writer, 400)
            elif route == b"OF-SF/1 GAME\n":
                await self.game(reader, writer)
        except (EOFError, OSError, ValueError, DecodeError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            pass
        finally:
            self.connected -= 1
            await close(writer)


async def loopback(port):
    return await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 6)


async def transfer(reader, writer, timeout):
    while chunk := await asyncio.wait_for(reader.read(65536), timeout):
        writer.write(chunk)
        await asyncio.wait_for(writer.drain(), timeout)


async def reject(writer, status):
    reason = {400:"Bad Request",404:"Not Found",413:"Content Too Large",429:"Too Many Requests"}[status]
    writer.write(f"HTTP/1.0 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
    await writer.drain()


async def close(writer):
    writer.close()
    with suppress(OSError, asyncio.TimeoutError):
        await asyncio.wait_for(writer.wait_closed(), 2)


def tls(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


async def listen(gate, host, port, context):
    return await asyncio.start_server(gate.accept, host, port, ssl=context, ssl_handshake_timeout=7, limit=8192)


def arguments(commands):
    cmd = commands.add_parser("player-gateway", help="Serve player accounts and game sessions over authenticated TLS")
    cmd.add_argument("--listen", default="0.0.0.0")
    cmd.add_argument("--port", type=int, default=18443)
    cmd.add_argument("--cert", type=Path, required=True)
    cmd.add_argument("--key", type=Path, required=True)
    cmd.add_argument("--max-connections", type=int, default=128)


def run(args, endpoints):
    host = str(ipaddress.ip_address(args.listen))
    if not ipaddress.ip_address(endpoints["bind"]).is_loopback:
        raise ValueError("Keep the realm services bound to loopback when using the public player gateway")
    if not 1 <= args.port <= 65535 or args.port in (endpoints["http_port"],endpoints["game_port"]):
        raise ValueError("The player gateway needs a separate port in 1..65535")
    if not 1 <= args.max_connections <= 2048:
        raise ValueError("Choose 1..2048 simultaneous gateway connections")
    context = tls(args.cert,args.key)
    gate = Gate(endpoints["http_port"],endpoints["game_port"],args.max_connections,player_gateway_peer.load(args.state))

    async def serve():
        async with await listen(gate,host,args.port,context) as server:
            print(f"OF_SF player gateway listening on {host}:{args.port}",flush=True)
            await server.serve_forever()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
