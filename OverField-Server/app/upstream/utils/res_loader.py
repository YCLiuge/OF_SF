"""Version-scoped resource snapshots and shared indices for national gameplay tables."""
from collections.abc import Mapping
import json
from pathlib import Path
from threading import RLock


# These tables are consumed by startup or a new player's first native login.
# Retain the already validated objects so the first player does not have to decode them again.
_LOGIN_TABLES = frozenset({
    "Achieve", "Action", "Activity", "ActivityGift", "ActivityQuest", "ActivityRegular",
    "ActivitySign", "ActivityTurntable", "BattlePass", "BlessingTree", "Character", "Chat",
    "CollectionItem", "DailyTask", "Explore", "Expression", "Fashion", "Flag", "GlobalConfig",
    "Head", "HomeChairSeats", "HomeItem", "Item", "Level", "PlayerUnlock", "Quest", "Scene",
    "ScenesConfigAsset", "Story", "Weapon",
})


class ResourceCatalog(Mapping):
    def __init__(self):
        self._tables = {}
        self._indices = {}
        self._lock = RLock()

    def load(self, resources):
        # Keep an immutable snapshot of the files. Invalid JSON must still fail at startup.
        tables = {}
        for path in sorted((Path(resources) / "data").glob("*.json")):
            raw = path.read_bytes()
            decoded = json.loads(raw)
            tables[path.stem] = decoded if path.stem in _LOGIN_TABLES else raw
        if not {"ScenesConfigAsset", "Quest", "Character", "Flag", "Item"} <= tables.keys():
            raise ValueError("Missing gameplay resources")
        with self._lock:
            self._tables = tables
            self._indices.clear()

    def __getitem__(self, name):
        value = self._tables[name]
        if isinstance(value, bytes):
            with self._lock:
                value = self._tables[name]
                if isinstance(value, bytes):
                    value = self._tables[name] = json.loads(value)
        return value

    def __iter__(self):
        return iter(self._tables)

    def __len__(self):
        return len(self._tables)

    def __contains__(self, name):
        return name in self._tables

    def index(self, resource, group, key="i_d"):
        owner = self[resource][group]
        marker = (resource, group, key)
        entry = self._indices.get(marker)
        if entry is not None and entry[0] is owner:
            return entry[1]
        with self._lock:
            # Another reader may have built this index, or load() may have installed a new snapshot.
            owner = self[resource][group]
            entry = self._indices.get(marker)
            if entry is None or entry[0] is not owner:
                rows = owner["datas"] if isinstance(owner, dict) else owner
                entry = (owner, {row[key]: row for row in rows if row.get(key)})
                self._indices[marker] = entry
            return entry[1]


res = ResourceCatalog()
init = res.load
