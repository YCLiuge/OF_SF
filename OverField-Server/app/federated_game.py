"""Project remote public-scene presence into the normal native Unity session."""
from threading import Event, Thread
from time import monotonic, time_ns
import logging
import os

import realm_uids
from federated_scene import Actor, LEASE, SceneOwner, actors_from
from network.msg_id import MsgId
from proto import net_pb2 as pb

log = logging.getLogger("overfield.world")
INTERVAL = 0.3


def configured(game):
    path = os.environ.get("OF_FEDERATION_CONFIG")
    if not path:
        return None
    from federation_world import World
    return SceneBridge(game, World(game.data_dir, path))


def coordinates(vec):
    if vec.decimal_places > 9:
        return 0, 0, 0, 0
    return vec.x, vec.y, vec.z, vec.decimal_places


def native_vector(values):
    x, y, z, scale = values
    return pb.Vector3(x=x, y=y, z=z, decimal_places=scale)


def scene_actor(session):
    lead = session.scene_player.team.char1
    if not lead.char_id:
        return None
    name = "".join(c for c in session.player_name[:64] if ord(c) >= 32) or str(session.player_id)
    return Actor(realm_uids.wire_uid(session.player_id), session.scene_id, session.channel_id,
                 name, lead.char_id, lead.char_lv, lead.weapon_id,
                 coordinates(lead.pos), coordinates(lead.rot))


def publish_changes(session, previous, visible):
    changes = pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
    moves = pb.PlayerSceneSyncDataNotice(status=pb.StatusCode_OK)
    for uid in previous.keys() - visible.keys():
        changes.data.add(player_id=uid).server_data.add(action_type=pb.SceneActionType_LEAVE)
    for uid, actor in visible.items():
        old = previous.get(uid)
        if actor == old:
            continue
        if old is None or (old.name, old.character, old.weapon, old.level) != (
                actor.name, actor.character, actor.weapon, actor.level):
            if old is not None:
                changes.data.add(player_id=uid).server_data.add(action_type=pb.SceneActionType_LEAVE)
            entry = changes.data.add(player_id=uid).server_data.add(action_type=pb.SceneActionType_ENTER)
            player = entry.player
            player.player_id = uid
            player.player_name = actor.name
            lead = player.team.char1
            lead.char_id, lead.char_lv, lead.weapon_id = actor.character, actor.level, actor.weapon
        else:
            lead = moves.data.add(player_id=uid).data.add().char_recorder_data_lst.add(char_id=actor.character)
        lead.pos.CopyFrom(native_vector(actor.position))
        lead.rot.CopyFrom(native_vector(actor.rotation))
    for kind, message in ((MsgId.ServerSceneSyncDataNotice, changes), (MsgId.PlayerSceneSyncDataNotice, moves)):
        if message.data:
            # These are already native wire actor IDs belonging to disjoint remote nodes.
            session.send(kind, message.SerializeToString(), 0, is_bin=True)


class SceneBridge:
    def __init__(self, game, world):
        self.game = game
        self.world = world
        rules = game.realm_rules
        self.channels = rules.public_min, rules.public_max
        self.owner = SceneOwner(world, self.channels)
        self.local_range = world.uid_ranges[world.node_id]
        if not self.local_range[0] <= rules.uid_start <= self.local_range[1]:
            raise ValueError("Set the realm uid_start inside its federation UID range")
        if any(first <= rules.public_max for first, last in world.uid_ranges.values()):
            raise ValueError("World actor IDs must be above public channel labels")
        self.stop_event = Event()
        self.threads = []

    def check_uid(self, pid):
        first, last = self.local_range
        if not first <= realm_uids.wire_uid(pid) <= last:
            raise ValueError("Native character ID falls outside this world's home node range")

    def start(self, conn):
        if not set(self.world.owners) <= self.game.scene_ids:
            raise ValueError("Unknown native scene in the world topology")
        for pid, in conn.execute("SELECT player_id FROM private_slots UNION SELECT player_id FROM players WHERE player_id>0"):
            self.check_uid(pid)
        # Keep a source's generations ordered across clean and unclean process restarts.
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS world_boot (node TEXT PRIMARY KEY, epoch INTEGER NOT NULL)")
            conn.execute("INSERT INTO world_boot VALUES (?,?) ON CONFLICT(node) DO UPDATE SET epoch=max(epoch+1,excluded.epoch)",
                         (self.world.node_id, time_ns()))
            self.epoch = conn.execute("SELECT epoch FROM world_boot WHERE node=?", (self.world.node_id,)).fetchone()[0]
        self.world.start(self.owner.exchange)
        try:
            for node in set(self.world.owners.values()):
                thread = Thread(target=self.loop, args=(node,), name=f"scene-owner-{node}", daemon=True)
                thread.start()
                self.threads.append(thread)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.stop_event.set()
        self.world.close()
        for thread in self.threads:
            thread.join()
        self.threads.clear()

    def capture(self, scenes):
        result = []
        with self.game.lock:
            for (scene, channel), members in self.game.scene_members.items():
                if scene not in scenes or not self.channels[0] <= channel <= self.channels[1]:
                    continue
                for session in members:
                    if session.running:
                        actor = scene_actor(session)
                        if actor is not None:
                            result.append(actor)
        return result

    def deliver(self, scenes, views):
        by_room = {}
        for actors in views.values():
            for actor in actors.values():
                by_room.setdefault(actor.room, {})[actor.uid] = actor
        with self.game.lock:
            for room, members in self.game.scene_members.items():
                if room[0] not in scenes or not self.channels[0] <= room[1] <= self.channels[1]:
                    continue
                current = by_room.get(room, {})
                for session in members:
                    if session.running:
                        previous = getattr(session, "world_view", None)
                        if previous is not current and (previous or current):
                            publish_changes(session, previous or {}, current)
                        session.world_view = current
            self.game._publish_pending()

    def loop(self, node):
        from federation_world import WorldError
        scenes = {scene for scene, owner in self.world.owners.items() if owner == node}
        previous_rooms, revisions, views = set(), {}, {}
        boot = None
        sequence = 0
        published = None
        last_ok = 0
        had_players = False
        failed = False

        def call(body):
            if node == self.world.node_id:
                return self.owner.exchange(node, body)
            return self.world.call(node, body)

        while not self.stop_event.is_set():
            actors = self.capture(scenes)
            if actors or had_players:
                sequence += 1
                rooms = {actor.room for actor in actors}
                try:
                    outbound = {actor.uid: actor for actor in actors}
                    request = {"epoch": self.epoch, "sequence": sequence}
                    if published == outbound and boot is not None:
                        request.update(op="scene.renew", boot=boot)
                    else:
                        request.update(op="scene.publish", actors=[actor.wire() for actor in actors])
                    reply = call(request)
                    version, sources = reply["boot"], reply["sources"]
                    if not isinstance(version, str) or not isinstance(sources, dict) or not sources.keys() <= self.world.uid_ranges.keys():
                        raise ValueError("Invalid presence reply")
                    next_views, next_revisions = {}, {}
                    for peer, revision in sources.items():
                        if peer == self.world.node_id or type(revision) is not int or revision <= 0:
                            raise ValueError("Invalid presence revision")
                        if rooms == previous_rooms and version == boot and revisions.get(peer) == revision:
                            next_views[peer], next_revisions[peer] = views[peer], revision
                        else:
                            row = call({"op":"scene.read", "source":peer})
                            next_views[peer] = actors_from(row["actors"], self.world.uid_ranges[peer], scenes, self.channels)
                            next_revisions[peer] = row["revision"]
                    boot, revisions, views, previous_rooms = version, next_revisions, next_views, rooms
                    published = outbound
                    had_players = bool(actors)
                    last_ok = monotonic()
                    if failed:
                        log.info("Public scene shard %s reconnected", node)
                    failed = False
                except (WorldError, OSError, ValueError, TypeError, KeyError):
                    if not failed and not self.stop_event.is_set():
                        log.warning("Public scene shard %s unavailable", node)
                    failed = True
                    published = None  # An owner restart or an expired lease requires the complete roster.
                    had_players = bool(actors) or monotonic() < last_ok + LEASE
                    if monotonic() >= last_ok + LEASE:
                        boot, revisions, views = None, {}, {}
                if not self.stop_event.is_set():
                    self.deliver(scenes, views)
            self.stop_event.wait(INTERVAL)
