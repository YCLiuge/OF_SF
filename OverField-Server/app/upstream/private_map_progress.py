"""Count the retail minimap's permanent collection and area markers by scene."""
from collections import Counter, defaultdict

from google.protobuf.message import DecodeError
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res

_sources = ()
_index = None
COLLECTION_TYPES = {1:2, 3:8, 5:6, 6:1, 7:3, 8:4, 9:5}
AREA = 12


def catalog():
    global _sources, _index
    sources = (res["StoryMapPoints"], res["Scene"])
    if _sources and all(a is b for a,b in zip(sources,_sources)):
        return _index
    capacities = {}
    for row in sources[1]["scene_progress"]["datas"]:
        capacities[row["i_d"]] = {r.get("new_progress_type",0):r.get("total",0)
                                  for r in row["scene_progress_info"]}
    collectibles = defaultdict(dict)
    devices = set()
    for scene, groups in sources[0].items():
        sid = int(scene)
        for group, markers in groups.items():
            kind = int(group)
            for point in markers:
                if kind == AREA:
                    devices.add((sid,point))
                else:
                    collectibles[point][sid] = kind
    _sources, _index = sources, (capacities, collectibles, devices)
    return _index


def _get(pid, ns, key):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k=?", (pid,ns,key)).fetchone()
    return int(row[0]) if row else 0


def _put(pid, ns, key, value):
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,?)",
                    (pid,ns,key,str(value)))


def _mark(pid, scene, kind, point):
    return db.conn.execute("INSERT OR IGNORE INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,?)",
                           (pid,f"map_points:{scene}",f"{kind}:{point}", "1")).rowcount


def rebuild(pid):
    # The historical collections table has no scene column. Only an unambiguous indexed ID
    # of the correct native collection type and status constitutes recoverable evidence.
    if _get(pid,"map_points","indexed_v1"):
        return
    caps, markers, devices = catalog()
    totals = Counter()
    for cid, kind, blob in db.conn.execute(
            "SELECT item_id,collection_type,item_blob FROM collections WHERE player_id=?", (pid,)):
        locations = markers.get(cid,{})
        if len(locations) != 1:
            continue
        (scene,group), = locations.items()
        if COLLECTION_TYPES.get(group) != kind:
            continue
        try:
            item = pb.PBCollectionRewardData.FromString(blob)
        except DecodeError:
            continue
        if item.item_id == cid and (item.status == pb.Reward or kind not in (8, 9)):
            totals[scene,group] += _mark(pid,scene,group,cid)
    for scene, device, blob in db.conn.execute("SELECT scene_id,area_id,area_blob FROM areas WHERE player_id=?", (pid,)):
        if (scene,device) not in devices:
            continue
        try:
            area = pb.AreaData.FromString(blob)
        except DecodeError:
            continue
        historical_reward = db.conn.execute(
            "SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='area_first_unlock' AND key=?",
            (pid,str(device))).fetchone()
        if area.area_id == device and (area.area_state == pb.AreaData.Unlock or historical_reward):
            totals[scene,AREA] += _mark(pid,scene,AREA,device)
    recovered = Counter()
    for (scene,group), count in totals.items():
        recovered[scene] += min(count, caps[scene][group])
    for scene,count in recovered.items():
        # An imported aggregate has no per-point provenance. Recovered points may already
        # be represented in it, so only raise its lower bound on the initial migration.
        if count > _get(pid,"ach",f"exp:{scene}"):
            _put(pid,"ach",f"exp:{scene}",count)
    _put(pid,"map_points","indexed_v1",1)


def points(pid, scene):
    rebuild(pid)
    return _get(pid,"ach",f"exp:{scene}")


def _record(pid, scene, kind, point):
    if not _mark(pid,scene,kind,point):
        return
    caps = catalog()[0][scene]
    group_count, = db.conn.execute("SELECT COUNT(*) FROM player_kv WHERE player_id=? AND ns=? AND k LIKE ?",
                                    (pid,f"map_points:{scene}", f"{kind}:%")).fetchone()
    current = _get(pid,"ach",f"exp:{scene}")
    if group_count <= caps[kind] and current < sum(caps.values()):
        _put(pid,"ach",f"exp:{scene}",current+1)


def collectible(item):
    return catalog()[1].get(item,{})


def collected(session, item, collection_type):
    if session.dungeon[0]:
        return
    scene = session.scene_id
    group = collectible(item).get(scene)
    if group is not None and COLLECTION_TYPES[group] == collection_type:
        _record(session.player_id,scene,group,item)
        from private_story_objectives import explored
        explored(session,scene)


def device_opened(session, device):
    scene = session.scene_id
    if not session.dungeon[0] and (scene,device) in catalog()[2]:
        _record(session.player_id,scene,AREA,device)
        from private_story_objectives import explored
        explored(session,scene)
