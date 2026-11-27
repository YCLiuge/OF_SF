"""Direct authenticated RPC between the fixed owners of a world's native scenes."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import socket
import socketserver
import ssl
import struct
from threading import BoundedSemaphore, Condition, Event, Lock, Thread
import time

from federation_topology import Topology, unique_keys

MAX_BODY = 256 * 1024
TIMEOUT = 3
IDLE = 20
WIRE = "OF-SF/WORLD/1"


class WorldError(RuntimeError):
    """The peer could not authenticate, complete, or acknowledge the request."""


def send_frame(sock, value, limit=MAX_BODY):
    body = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > limit:
        raise ValueError("World frame too large")
    sock.settimeout(TIMEOUT)
    sock.sendall(struct.pack("!I", len(body)) + body)


def read_exact(sock, length, deadline):
    buf = bytearray(length)
    view = memoryview(buf)
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("World frame timed out")
        sock.settimeout(remaining)
        count = sock.recv_into(view)
        if not count:
            raise EOFError
        view = view[count:]
    return buf


def read_frame(sock, limit=MAX_BODY, idle=False):
    # Allow idle pooled streams, then apply an absolute deadline to the entire frame.
    first = read_exact(sock, 1, time.monotonic() + (IDLE if idle else TIMEOUT))
    deadline = time.monotonic() + TIMEOUT
    size, = struct.unpack("!I", first + read_exact(sock, 3, deadline))
    if not 1 <= size <= limit:
        raise ValueError("Invalid world frame length")
    value = json.loads(read_exact(sock, size, deadline).decode("utf-8"),
                       object_pairs_hook=unique_keys, parse_constant=invalid_number)
    if not isinstance(value, dict):
        raise ValueError("Expected a world message")
    return value


def invalid_number(value):
    raise ValueError("Non-finite number")


def tls_context(topo, client):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT if client else ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    certs = "\n".join(node.pem for name, node in topo.nodes.items() if name != topo.node_id)
    if certs:
        context.load_verify_locations(cadata=certs)
    context.load_cert_chain(topo.nodes[topo.node_id].cert, topo.key)
    return context


class Listener(socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, world):
        self.world = world
        topo = world.topology
        port = topo.nodes[topo.node_id].port
        self.address_family = socket.getaddrinfo(topo.bind, port, type=socket.SOCK_STREAM)[0][0]
        workers = max(8, min(128, len(topo.nodes) * 4 + 4))
        self.slots = BoundedSemaphore(workers)
        super().__init__((topo.bind, port), None)
        self.executor = ThreadPoolExecutor(workers, thread_name_prefix="world-peer")

    def process_request(self, sock, address):
        if not self.slots.acquire(blocking=False):
            sock.close()
            return
        self.executor.submit(self.handle_peer, sock)

    def handle_peer(self, sock):
        try:
            self.world.accept(sock)
        except (EOFError, OSError, ValueError, RecursionError, WorldError):
            pass
        finally:
            sock.close()
            self.slots.release()

    def server_close(self):
        super().server_close()
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)


def disconnect(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


class World:
    def __init__(self, state, config_path):
        # Game and presence remain at their respective owners. Transport stores no player data.
        self.topology = topo = Topology(config_path)
        self.node_id = topo.node_id
        self.uid_ranges = dict(topo.uid_ranges)
        self.owners = dict(topo.owners)
        self._hello = {"wire": WIRE, "node": self.node_id, "manifest": topo.digest}
        self._client_tls = tls_context(topo, True)
        self._server_tls = tls_context(topo, False)
        self._lock = Lock()
        self._drained = Condition(self._lock)
        self._calls = 0
        self._stopping = Event()
        self._active = set()
        self._pools = {peer: [] for peer in topo.nodes if peer != self.node_id}
        self._outbound = {peer: BoundedSemaphore(4) for peer in self._pools}
        self._inbound = {peer: BoundedSemaphore(4) for peer in self._pools}
        self._listener = None

    def start(self, handler):
        if self._listener is not None:
            raise WorldError("World listener is already running")
        self._handler = handler
        self._stopping.clear()
        self._listener = Listener(self)
        self._thread = Thread(target=self._listener.serve_forever, kwargs={"poll_interval": 0.1},
                              name="world-listen", daemon=True)
        self._thread.start()

    def close(self):
        listener = self._listener
        if listener is None:
            return
        with self._lock:
            self._stopping.set()
        listener.shutdown()
        with self._lock:
            for sock in list(self._active):
                disconnect(sock)
            for pool in self._pools.values():
                pool.clear()
        listener.server_close()
        self._thread.join()
        with self._drained:
            self._drained.wait_for(lambda: self._calls == 0)
            self._active.clear()
            self._listener = None

    def track(self, sock):
        with self._lock:
            if self._stopping.is_set():
                disconnect(sock)
                raise WorldError("World is stopping")
            self._active.add(sock)

    def release(self, sock):
        with self._lock:
            self._active.discard(sock)
        disconnect(sock)

    def check_hello(self, sock, expected=None):
        fingerprint = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
        peer = self.topology.by_pin.get(fingerprint)
        if peer not in self._pools or (expected is not None and peer != expected):
            raise WorldError("Peer certificate does not match the configured node")
        hello = read_frame(sock, 512)
        if hello != {**self._hello, "node": peer}:
            raise WorldError("Peer identity, protocol or world routing manifest differs")
        return peer

    def accept(self, sock):
        sock.settimeout(TIMEOUT)
        with self._server_tls.wrap_socket(sock, server_side=True) as channel:
            self.track(channel)
            try:
                peer = self.check_hello(channel)
                if not self._inbound[peer].acquire(blocking=False):
                    return
                try:
                    send_frame(channel, self._hello, 512)
                    while not self._stopping.is_set():
                        data = read_frame(channel, idle=True)
                        if data == {"ping": 1}:
                            send_frame(channel, {"pong": 1})
                            continue
                        try:
                            if set(data) != {"rpc"} or not isinstance(data["rpc"], dict):
                                raise ValueError("Invalid world operation")
                            reply = self._handler(peer, data["rpc"])
                            if not isinstance(reply, dict):
                                raise ValueError("Handler must return an object")
                            send_frame(channel, {"ok": reply})
                        except (ValueError, RuntimeError, KeyError, TypeError, RecursionError):
                            send_frame(channel, {"error": "rejected"})
                finally:
                    self._inbound[peer].release()
            finally:
                self.release(channel)

    def connect(self, peer):
        with self._lock:
            pool = self._pools[peer]
            while pool:
                stamp, channel = pool.pop()
                if time.monotonic() - stamp < IDLE / 2:
                    return channel
                self._active.discard(channel)
                disconnect(channel)
        node = self.topology.nodes[peer]
        with socket.create_connection((node.host, node.port), TIMEOUT) as raw:
            channel = self._client_tls.wrap_socket(raw, server_hostname=node.host)
        try:
            self.track(channel)
            send_frame(channel, self._hello, 512)
            self.check_hello(channel, peer)
            return channel
        except BaseException:
            self.release(channel)
            raise

    def probe(self, peer_id):
        """Perform an authenticated, read-only check using this node's credentials."""
        if peer_id not in self._outbound:
            raise WorldError("Unknown peer")
        channel = None
        try:
            channel = self.connect(peer_id)
            send_frame(channel, {"ping": 1})
            if read_frame(channel) != {"pong": 1}:
                raise WorldError("World peer rejected health check")
        except (EOFError, OSError, ValueError) as exc:
            raise WorldError("World peer health check failed") from exc
        finally:
            if channel is not None:
                self.release(channel)

    def call(self, peer_id, payload):
        gate = self._outbound.get(peer_id)
        if gate is None or not isinstance(payload, dict):
            raise WorldError("Invalid world peer or request")
        with self._lock:
            if self._listener is None or self._stopping.is_set():
                raise WorldError("World is not running")
            self._calls += 1
        channel = None
        acquired = False
        try:
            acquired = gate.acquire(timeout=TIMEOUT)
            if not acquired:
                raise WorldError("World peer is busy")
            channel = self.connect(peer_id)
            send_frame(channel, {"rpc": payload})
            reply = read_frame(channel)
            if set(reply) != {"ok"} or not isinstance(reply["ok"], dict):
                raise WorldError("World peer rejected request")
            with self._lock:
                if not self._stopping.is_set():
                    self._pools[peer_id].append((time.monotonic(), channel))
                    channel = None
            return reply["ok"]
        except (EOFError, OSError, ValueError, TypeError, RecursionError) as exc:
            raise WorldError("World peer unavailable or exchange incomplete") from exc
        finally:
            if channel is not None:
                self.release(channel)
            if acquired:
                gate.release()
            with self._drained:
                self._calls -= 1
                self._drained.notify_all()
