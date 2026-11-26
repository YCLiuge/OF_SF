"""Verified Windows client build and resource identities shared by the player and asset mirror."""
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import NamedTuple


class ResourcePatch(NamedTuple):
    digest: str
    plaza_bundle: str
    bundle_count: int


class ResourceSeries(NamedTuple):
    build: str
    base_digest: str
    patches: dict[str, ResourcePatch]
    edit_plaza: bool = True


BUILD = "2026-10-21-14-05-50"
PATCH = "2026-11-07-22-39-35"
NOVEMBER_PATCH = "2026-11-25-10-44-03"
EVENING_PATCH = "2026-11-25-19-54-41"
TAPTAP_RESOURCES = ResourceSeries(
    BUILD, "cb7dd3bb6e9ed0702ceb3c1ca96f43998d4af904327ba4c349224947d976f8be",
    {
        PATCH: ResourcePatch("8c467ba267e06ffa4cd6e3dcb2326a6afc403c7f5e1c2dc18bcc1e83410204d9", "e49d461782ed9bf3309d2c35662e132a", 8936),
        NOVEMBER_PATCH: ResourcePatch("7c1d25a4b2efcc802a45e841cf95ae8438f317d68dd1e37ce2c6eee8ab707de6", "4479579c8fb33fb720ec2dadebe34778", 8936),
        EVENING_PATCH: ResourcePatch("c5c6219f13c7cbac864e4cb04929979997fbe22e0d137ce5023edc89f52668c5", "63fd83b3b51cecbc1d604f40f76db609", 8936),
    },
)
# Only builds with a tested game resource mirror may advertise resources.
_FINGERPRINT_FILES = {"launcher.exe", "GameAssembly.dll", "launcher_Data/data.unity3d"}


@lru_cache(maxsize=1)
def channel_records():
    """Recognition metadata cannot authorize a launch or a game resource mount."""
    records = []
    for path in sorted(Path(__file__).with_name("client_builds").glob("*.json")):
        record = json.loads(path.read_bytes())
        hashes = record["files"]
        if (record["id"] != path.stem or not record["channel"]
                or not re.fullmatch(r"[0-9.-]{1,40}", record["build"])
                or set(hashes) != _FINGERPRINT_FILES
                or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())):
            raise ValueError("客户端指纹目录数据无效。")
        for name, digest in record.get("verified_sdk", {}).items():
            if (not re.fullmatch(r"launcher_Data/Plugins/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:dll|exe)", name)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("客户端 SDK 指纹目录数据无效。")
        records.append(record)
    return tuple(records)

def resource_catalog():
    catalog = {BUILD: TAPTAP_RESOURCES}
    for record in channel_records():
        # Independent authorization for a local, read-only resource mirror.
        if record["id"] not in ("bilibili-cn-1.1.12", "steam-global-26178963") or not record.get("resources"):
            continue
        resources = record["resources"]
        count = resources["bundle_count"]
        base = resources["base_sha256"]
        patches = {}
        if type(count) is not int or not 0 < count <= 100000 or not re.fullmatch(r"[0-9a-f]{64}", base):
            raise ValueError("Invalid client resource descriptor")
        for stamp, patch in resources["patches"].items():
            patch_count = patch.get("bundle_count", count)
            if (type(patch_count) is not int or not 0 < patch_count <= 100000
                    or not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}){5}", stamp)
                    or not re.fullmatch(r"[0-9a-f]{64}", patch["sha256"])
                    or not re.fullmatch(r"[0-9a-f]{32}", patch["plaza_bundle"])):
                raise ValueError("Invalid resource manifest identity")
            patches[stamp] = ResourcePatch(patch["sha256"], patch["plaza_bundle"], patch_count)
        if not patches or record["build"] in catalog:
            raise ValueError("Ambiguous or empty client resource series")
        catalog[record["build"]] = ResourceSeries(record["build"], base, patches,
                                                  edit_plaza=record["id"] == "bilibili-cn-1.1.12")
    return catalog


RESOURCE_SERIES = resource_catalog()
