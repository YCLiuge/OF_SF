"""Native 4000 ten-pull fashion banner, including its per-player boost and item discovery rules."""
import secrets

from utils.res_loader import res
from private_gacha_rewards import record
from private_shop import in_window,now
from proto import net_pb2 as pb
from realm_gacha import FASHION
from utils import db


# The 13 basic weapon names in StringGacha 4000, resolved to their national purple item rows.
BASIC_WEAPONS=(1101401,1101402,1101404,1102401,1103401,1201401,1201403,1202401,1302401,
               1303401,1401401,1402401,1403401)
PURPLE,GOLD,OUTFIT=40002,40003,40004


def setup():
    db.conn.execute("""CREATE TABLE IF NOT EXISTS private_fashion_wishes(
        player INTEGER PRIMARY KEY, pool INTEGER NOT NULL, item INTEGER NOT NULL,
        misses INTEGER NOT NULL DEFAULT 0)""")


def choice(pool,item):
    if pool not in (PURPLE,GOLD,OUTFIT):return None
    return next((r for r in res.index("Gacha","reward_pool")[pool]["items"]
                 if r["item_i_d"]==item and r.get("is_optional")),None)


def current(player):
    row=db.conn.execute("SELECT pool,item,misses FROM private_fashion_wishes WHERE player=?",(player,)).fetchone()
    if row:
        candidate=choice(row[0],row[1])
        if candidate and in_window(candidate,now()):
            return row
    return 0,0,0


def rate(misses):
    start,step=map(int,res.index("GlobalConfig","config","key")["Gacha_OptionalUp_Param"]["values"])
    return min(100,start+step*misses)


def info(pid,count):
    group,item,misses=current(pid)
    return pb.GachaInfo(gacha_id=FASHION,gacha_times=count,choose_pool_id=group,optional_up_item=item,
                        optional_value=rate(misses) if item else 0)


def select(s,req,rsp):
    from private_gacha import active,onboarding
    if req.gacha_id!=FASHION or FASHION not in active() or s.dungeon[0] or onboarding(s):
        return pb.StatusCode_BAD_REQ
    if not req.item_id:
        previous=current(s.player_id)
        if req.poo_id not in (0,previous[0]):return pb.StatusCode_BAD_REQ
        db.conn.execute("DELETE FROM private_fashion_wishes WHERE player=?",(s.player_id,))
    else:
        row=choice(req.poo_id,req.item_id)
        if not row or not in_window(row,now()):return pb.StatusCode_BAD_REQ
        db.conn.execute("""INSERT INTO private_fashion_wishes(player,pool,item) VALUES(?,?,?)
            ON CONFLICT(player) DO UPDATE SET pool=excluded.pool,item=excluded.item,misses=0
            WHERE pool!=excluded.pool OR item!=excluded.item""",(s.player_id,req.poo_id,req.item_id))
    rsp.info.CopyFrom(info(s.player_id,db.get_total_gacha_num(s.player_id,FASHION)))
    return pb.StatusCode_OK


_catalog_cache=None


def weapon_catalog():
    global _catalog_cache
    table=res.index("Weapon","weapon")
    pools=res.index("Gacha","reward_pool")
    if _catalog_cache is None or _catalog_cache[0] is not table or _catalog_cache[1] is not pools:
        families={tid:r.get("asset_full_name") for tid,r in table.items() if r.get("asset_full_name")}
        allowed={families[tid] for tid in BASIC_WEAPONS}
        allowed.update(families[r["item_i_d"]] for group in (GOLD,PURPLE)
                       for r in pools[group]["items"] if r.get("is_optional"))
        _catalog_cache=(table,pools,families,allowed)
    return _catalog_cache[2:]


def known_ids(pid):
    # Older inventories can predate the ownership ledger. Migrate just once, within the draw transaction.
    from private_collection_queries import remember_item
    marker=db.conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'migration','fashion_discovery_v1')",(pid,))
    if marker.rowcount:
        for name in ("items_s","items"):
            for raw, in db.conn.execute("SELECT item_detail_blob FROM "+name+" WHERE player_id=?",(pid,)):
                remember_item(pid,raw)
    return {int(k) for k, in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='got_ids'",(pid,))
            if str(k).isascii() and str(k).isdigit()}


def groups_for(known):
    source=res.index("Gacha","reward_pool")
    families,basic=weapon_catalog()
    allowed=basic.union(families[item] for item in known if item in families)
    sets={}
    for group in (40000,40001,PURPLE,GOLD,OUTFIT):
        sets[group]=[r["item_i_d"] for r in source[group]["items"] if
                     group not in (GOLD,PURPLE) or families.get(r["item_i_d"]) in allowed]
    return sets


def roll(weights,groups):
    value=secrets.randbelow(10000)
    for raw,weight in weights.items():
        value-=weight
        if value<0:return secrets.choice(groups[int(raw)])
    raise ValueError("Invalid fashion banner group weights")


def ten_items(weights,groups,selected,boost):
    # PC text defines the per-ten increase, but omits the server's order of rolls. Reserve an
    # outfit if needed; a successful wish may replace any other draw without breaking that floor.
    result=[roll(weights,groups) for _ in range(10)]
    outfits=set(groups[OUTFIT])
    if not any(t in outfits for t in result):
        result[-1]=secrets.choice(groups[OUTFIT])
    if selected and selected not in result and secrets.randbelow(100)<boost:
        replaceable=list(range(len(result)))
        if selected not in outfits:
            worn=[index for index,t in enumerate(result) if t in outfits]
            if len(worn)==1:replaceable.remove(worn[0])
        result[secrets.choice(replaceable)]=selected
    return result


def draw(s,req,rsp,pending,pool):
    if req.is_single:return pb.StatusCode_BAD_REQ
    cfg=res.index("Gacha","info")[FASHION]
    if cfg.get("method2_count")!=10:return pb.StatusCode_CFG_NOT_FOUND
    pending.balances.change(cfg["consume_item2_i_d"],-cfg["consume_item2_num"])
    pid=s.player_id
    known=known_ids(pid)
    _,wished,misses=current(pid)
    items=ten_items(pool["weights"],groups_for(known),wished,rate(misses))
    for tid in items:
        pending.grant({tid:1},rsp.items)
        outcome=rsp.items[-1]
        outcome.main_item.is_new=tid not in known
        known.add(tid)
        record(db,pid,FASHION,outcome)
    if wished:
        db.conn.execute("UPDATE private_fashion_wishes SET misses=? WHERE player=?",
                        (0 if wished in items else min(100,misses+1),pid))
    rsp.info.CopyFrom(info(pid,db.get_total_gacha_num(pid,FASHION)))
    return pb.StatusCode_OK
