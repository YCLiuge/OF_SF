"""Private topology for direct, certificate-pinned scene peers."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import ssl

NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
MAX_NODES = 32


def unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate JSON key")
        data[key] = value
    return data


def bounded_integer(value, low, high):
    return type(value) is int and low <= value <= high


def node_name(value):
    return isinstance(value, str) and NAME.fullmatch(value) is not None


@dataclass(frozen=True)
class Node:
    host: str
    port: int
    pin: str
    pem: str
    cert: Path
    uids: tuple


class Topology:
    def __init__(self, path):
        path = Path(path).resolve()
        with path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("World configuration exceeds 128 KiB")
        c = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
        if not isinstance(c, dict) or set(c) != {"world", "node", "key", "bind", "nodes", "scenes"}:
            raise ValueError("Expected world, node, key, bind, nodes and scenes")
        self.world = c["world"]
        self.node_id = c["node"]
        self.bind = c["bind"]
        if (not node_name(self.world) or not node_name(self.node_id)
                or not isinstance(self.bind, str) or not self.bind):
            raise ValueError("Invalid world, local node or bind address")
        self.key = path.parent / c["key"]
        nodes = c["nodes"]
        if not isinstance(nodes, dict) or not 1 <= len(nodes) <= MAX_NODES or self.node_id not in nodes:
            raise ValueError("World requires 1..32 nodes including this node")
        self.nodes = {}
        self.by_pin = {}
        for name, node in nodes.items():
            if not node_name(name) or not isinstance(node, dict) or set(node) != {"host", "port", "cert", "uids"}:
                raise ValueError("Invalid world node configuration")
            host, port, uids = node["host"], node["port"], node["uids"]
            if not (isinstance(host, str) and 0 < len(host) <= 253 and bounded_integer(port, 1, 65535)
                    and isinstance(uids, list) and len(uids) == 2
                    and all(bounded_integer(n, 1, 2**32 - 1) for n in uids) and uids[0] <= uids[1]):
                raise ValueError("Invalid peer host, port or UID interval")
            for other in self.nodes.values():
                if uids[0] <= other.uids[1] and other.uids[0] <= uids[1]:
                    raise ValueError("Node UID intervals must be disjoint")
            cert = path.parent / node["cert"]
            pem = cert.read_text(encoding="ascii").strip()
            if pem.count("-----BEGIN CERTIFICATE-----") != 1 or not pem.endswith("-----END CERTIFICATE-----"):
                raise ValueError("Each world node needs one public leaf certificate")
            pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            if pin in self.by_pin:
                raise ValueError("World nodes must have different certificates")
            self.by_pin[pin] = name
            self.nodes[name] = Node(host, port, pin, pem, cert, tuple(uids))
        self.uid_ranges = {name: node.uids for name, node in self.nodes.items()}
        scenes = c["scenes"]
        if not isinstance(scenes, dict) or not 1 <= len(scenes) <= 4096:
            raise ValueError("Expected 1..4096 explicitly assigned scene IDs")
        self.owners = {}
        for scene, owner in scenes.items():
            if (not re.fullmatch("[1-9][0-9]{0,9}", scene) or int(scene) > 2**31-1
                    or not isinstance(owner,str) or owner not in self.nodes):
                raise ValueError("Invalid scene or unknown owner")
            self.owners[int(scene)] = owner
        # Local listening, NAT addresses and paths can differ on each machine.
        identity = {name: [node.pin, *node.uids] for name, node in self.nodes.items()}
        manifest = json.dumps([self.world, identity, scenes], sort_keys=True, separators=(",", ":"))
        self.digest = hashlib.sha256(manifest.encode("ascii")).hexdigest()
