"""Credit actual field discoveries to the national scene, item and cumulative collection goals."""
from collections import Counter, defaultdict

from private_dungeons import _table, report
from private_map_progress import collectible
from proto import net_pb2 as pb
from utils import db

# Types 8 and 9 both hand out item 103. Only type 8 is a crystal on the field.
# Photo souvenirs (9) and map-only markers (10/11) have no authored link to these goals.
CONDITIONS = {1:(12,91), 2:(13,98), 3:(14,94), 4:(15,93), 5:(16,100,141),
              6:(17,92), 7:(97,), 8:(95,)}
NAMESPACE = "field_collection_achievements"
_index = None


def rules():
    global _index
    achievements = _table("Achieve", "achieve")
    items = _table("CollectionItem", "collection_item")
    if _index and _index[0] is achievements and _index[1] is items:
        return _index[2], _index[3]
    scoped, exact = defaultdict(list), defaultdict(list)
    valid_conditions = {c for conditions in CONDITIONS.values() for c in conditions}
    scenes = _table("Scene", "scene")
    for aid, row in achievements.items():
        kind = row.get("new_achieve_condition")
        args, target = row.get("param", ()), row.get("count_param", 0)
        if target <= 0:
            continue
        if kind == 2 and len(args) == 1 and args[0] in items:
            exact[args[0]].append((aid,target))
        elif kind in valid_conditions and (not args or (len(args) == 1 and args[0] in scenes)):
            scoped[kind, args[0] if args else 0].append((aid,target))
    _index = (achievements, items, scoped, exact)
    return scoped, exact


def credit(session, counts, found):
    scoped, exact = rules()
    awards = [(aid,min(1,goal)) for item in found for aid,goal in exact.get(item,())]
    for (kind,scene), newly_found in counts.items():
        goals = [g for condition in CONDITIONS[kind] for g in scoped.get((condition,scene),())]
        if not goals:
            continue
        key = f"{kind}:{scene}"
        db.conn.execute("INSERT INTO player_kv VALUES(?,?,?,?) ON CONFLICT(player_id,ns,k) "
                        "DO UPDATE SET v=CAST(v AS INTEGER)+excluded.v",
                        (session.player_id,NAMESPACE,key,newly_found))
        total, = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k=?",
                                 (session.player_id,NAMESPACE,key)).fetchone()
        awards.extend((aid,min(int(total),goal)) for aid,goal in goals)
    report(session, awards)


def scopes(kind, scene):
    # On the original Chinese achievement panel the daytime garden (1), snow globe
    # (1001), and hotpot (1002) are separately named; a visit counts only in its map.
    if kind not in CONDITIONS:
        return ()
    return ((kind,scene),(kind,0)) if scene else ((kind,0),)


def restore(session):
    pid = session.player_id
    if db.conn.execute("SELECT 1 FROM player_kv WHERE player_id=? AND ns=? AND k='restored'",
                       (pid,NAMESPACE)).fetchone():
        return
    from private_collection_rewards import region
    known = _table("CollectionItem","collection_item")
    places = dict(db.conn.execute("SELECT CAST(k AS INTEGER),CAST(v AS INTEGER) FROM player_kv "
                                  "WHERE player_id=? AND ns='collection_scene'", (pid,)))
    counts, found = Counter(), set()
    rows = db.conn.execute("SELECT item_id,collection_type,item_blob FROM collections WHERE player_id=?", (pid,))
    for item, kind, raw in rows:
        if not known.get(item) or known[item]["new_collection_type"] != kind:
            continue
        data = pb.PBCollectionRewardData.FromString(raw)
        if data.item_id != item or (kind in (8,9) and data.status != pb.Reward):
            continue
        locations = collectible(item)
        if item in places:
            scene = places[item]
        elif len(locations) == 1:
            scene = next(iter(locations))
        elif not locations:
            scene = region(item)
        else:
            # Historical rows with ambiguous map ownership can only support global totals.
            scene = 0
        if scene < 0:
            continue
        found.add(item)
        counts.update(scopes(kind,scene))
    credit(session,counts,found)
    db.conn.execute("INSERT INTO player_kv VALUES(?,?,'restored',1)", (pid,NAMESPACE))


def discovered(session, item, kind):
    if not session.dungeon[0]:
        credit(session,Counter(scopes(kind,session.scene_id)),(item,))
