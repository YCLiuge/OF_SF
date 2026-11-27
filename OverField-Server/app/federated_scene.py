"""Authenticated, expiring player snapshots for public scenes owned by this node."""
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from uuid import uuid4


LEASE = 6.0
MAX_PLAYERS = 512
MAX_COUNTER = (1 << 63) - 1


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def vector(value):
    return (isinstance(value, list) and len(value) == 4
            and all(integer(n, -(1 << 31), (1 << 31) - 1) for n in value[:3])
            and integer(value[3], 0, 9))


@dataclass(frozen=True)
class Actor:
    uid: int
    scene: int
    channel: int
    name: str
    character: int
    level: int
    weapon: int
    position: tuple
    rotation: tuple

    @property
    def room(self):
        return self.scene, self.channel

    def wire(self):
        return [self.uid, self.scene, self.channel, self.name, self.character, self.level,
                self.weapon, list(self.position), list(self.rotation)]


def actors_from(rows, uid_range, scenes, public_channels):
    if not isinstance(rows, list) or len(rows) > MAX_PLAYERS:
        raise ValueError("Invalid number of scene actors")
    actors = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 9:
            raise ValueError("Invalid scene actor")
        uid, scene, channel, name, character, level, weapon, pos, rot = row
        if (not integer(uid, *uid_range) or uid in actors or not integer(scene, 1, 0xffffffff)
                or scene not in scenes or not integer(channel, *public_channels)
                or not isinstance(name, str) or not name or len(name) > 64
                or any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in name)
                or not integer(character, 1, 0xffffffff) or not integer(level, 0, 0xffffffff)
                or not integer(weapon, 0, 0xffffffff) or not vector(pos) or not vector(rot)):
            raise ValueError("Actor is outside the public scene or its home node range")
        actors[uid] = Actor(uid, scene, channel, name, character, level, weapon, tuple(pos), tuple(rot))
    return actors


@dataclass
class Publication:
    stamp: tuple
    revision: int
    expires: float
    actors: dict
    rooms: set


class SceneOwner:
    """Only the certificate-bound source may replace its own presence in this shard."""
    def __init__(self, world, public_channels, clock=monotonic):
        self.world = world
        self.scenes = {scene for scene, node in world.owners.items() if node == world.node_id}
        self.channels = public_channels
        self.clock = clock
        self.by_node = {}
        self.lock = Lock()
        self.version = 0
        self.boot = uuid4().hex

    def exchange(self, node, request):
        with self.lock:
            return self._exchange(node, request)

    def _exchange(self, node, request):
        now = self.clock()
        for state in self.by_node.values():
            if state.expires <= now:
                state.actors.clear()
                state.rooms.clear()
        if node not in self.world.uid_ranges or not isinstance(request, dict):
            raise ValueError("Unknown scene source")
        if request.get("op") == "scene.read" and set(request) == {"op", "source"}:
            source = request["source"]
            if not isinstance(source, str) or source not in self.world.uid_ranges or source == node:
                raise ValueError("Unknown remote scene source")
            reader = self.by_node.get(node)
            target = self.by_node.get(source)
            if not reader or not target or not reader.rooms.intersection(target.rooms):
                return {"actors": [], "revision": 0}
            return {"actors": [a.wire() for a in target.actors.values() if a.room in reader.rooms],
                    "revision": target.revision}
        operation = request.get("op")
        extra = "boot" if operation == "scene.renew" else "actors"
        if operation not in {"scene.publish", "scene.renew"} or set(request) != {"op", "epoch", "sequence", extra}:
            raise ValueError("Unknown scene operation")
        if not all(integer(request[key], 1, MAX_COUNTER) for key in ("epoch", "sequence")):
            raise ValueError("Invalid publisher generation")
        stamp = request["epoch"], request["sequence"]
        old = self.by_node.get(node)
        if old and stamp <= old.stamp:
            raise ValueError("Stale scene snapshot")
        if operation == "scene.renew":
            if request["boot"] != self.boot or not old or old.expires <= now or old.stamp[0] != stamp[0]:
                raise ValueError("Resubmit a full scene snapshot")
            old.stamp, old.expires = stamp, now + LEASE
            rooms = old.rooms
        else:
            actors = actors_from(request["actors"], self.world.uid_ranges[node], self.scenes, self.channels)
            rooms = {actor.room for actor in actors.values()}
            if not old or old.actors != actors:
                self.version += 1
                revision = self.version
            else:
                revision = old.revision
            self.by_node[node] = Publication(stamp, revision, now + LEASE, actors, rooms)
        return {"boot": self.boot, "sources": {peer: state.revision for peer, state in self.by_node.items()
                            if peer != node and rooms.intersection(state.rooms)}}
