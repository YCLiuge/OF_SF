"""Place owned treasure maps on original field gathering points and settle a completed dig."""
import random

from utils.res_loader import res
from private_equipment import load
from private_gameplay_transaction import run
from proto import net_pb2 as pb
from utils import db

# National rewards contain the prize contents; these pool-selection weights follow of-ps.
POOLS = {5070001:(7000030,7000040,7000050,7000060), 5070002:(7000080,7000090,7000100)}
_point_index = None


def field_points():
    global _point_index
    scenes = res.index("ScenesConfigAsset","Scenes","ID")
    if _point_index and _point_index[0] is scenes:
        return _point_index[1]
    result = {}
    for scene_id, scene in scenes.items():
        groups = scene.get("GatherPointSetInfo", ())
        if not groups:
            continue
        cells = {}
        for region in groups:
            origin = region["MinStartPoint"]
            size = region["CellSize"]
            for group in region.get("GatherPointSets", ()):
                if not group.get("LifeGatherPointers"):
                    continue
                group_id = group.get("Index", 0)
                cell = group.get("SetIndex", {})
                bounds = (origin.get("x",0) + cell.get("x",0)*size,
                          origin.get("z",0) + cell.get("z",0)*size, size)
                for point in group.get("LifeGatherPointers", ()):
                    if point.get("Index",0)>0:
                        cells[group_id,point["Index"]] = bounds
        if cells:
            result[scene_id] = (tuple(cells),cells)
    _point_index = scenes,result
    return result


def assign(player, treasure):
    fields = field_points()
    scene = treasure.scene_id
    if scene not in fields:
        # A new map can always be reached from the first field. Include other fields already visited.
        visited = [row[0] for row in db.conn.execute(
            "SELECT scene FROM private_poses WHERE player_id=? AND scene BETWEEN 2 AND 5",(player,))]
        scene = random.choice([1] + [sid for sid in visited if sid in fields])
    treasure.scene_id = scene
    treasure.gather_point_index,treasure.life_gather_point_index = random.choice(fields[scene][0])


def repair(player, detail):
    """Earlier packs gave map instances only a scene. Keep their UUID and valid destinations."""
    item = detail.main_item
    treasure = item.treasure
    if item.WhichOneof("item")!="treasure" or not treasure.instance_id or treasure.item_id not in POOLS:
        return
    fields = field_points()
    pair = treasure.gather_point_index,treasure.life_gather_point_index
    if treasure.scene_id in fields and pair in fields[treasure.scene_id][1]:
        return
    assign(player,treasure)
    db.set_item_detail(player,detail.SerializeToString(),instance_id=treasure.instance_id)


def equipped_shovel(session):
    # No shovel ID is sent in 4521. Use the field actor's stored personal tool.
    actor = session.scene_player.team.char1
    key = actor.character_appearance.shovel_instance_id
    detail = load(session.player_id,key,"weapon")
    if actor.is_dead or not detail:
        return None
    weapon = detail.main_item.weapon
    config = res.index("Shovel","shovel").get(weapon.weapon_id)
    if not config or config.get("item_i_d")!=detail.main_item.item_id or not weapon.durability:
        return None
    return config


def draw_reward(level,pool):
    groups = res.index("Reward","reward_pool").get(pool,{}).get("reward_pool_group",())
    choices = []
    for group in groups:
        entries = res.index("Reward","reward_item_pool").get(group["reward_pool_group_i_d"],{})
        choices.extend(row for row in entries.get("reward_item_pool_group",())
                       if row.get("min_level_limit",0)<=level<=row.get("max_level_limit",0xffffffff))
    if not choices:
        return None
    row = random.choice(choices)
    if row.get("need_items") or row["item_i_d"] not in res.index("Item","item"):
        return None
    qty = random.randint(row.get("item_min_count",1),row.get("item_max_count",1))
    return {row["item_i_d"]:qty} if qty else None


def settle(session,req,rsp,write):
    player = session.player_id
    detail = load(player,req.treasure_id,"treasure")
    if not detail:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    treasure = detail.main_item.treasure
    if treasure.item_id not in POOLS or treasure.item_id!=detail.main_item.item_id:
        return pb.StatusCode_BAD_REQ
    if session.dungeon[0] or getattr(session,"mp_room_team",0) or session.scene_id!=treasure.scene_id:
        return pb.StatusCode_BAD_REQ
    pair = treasure.gather_point_index,treasure.life_gather_point_index
    field = field_points().get(treasure.scene_id)
    bounds = field and field[1].get(pair)
    if not bounds:
        return pb.StatusCode_BAD_REQ
    x,z,cell = bounds
    pos = session.scene_player.team.char1.pos
    # The export retains grid bounds. The client raycasts and checks the precise point.
    # Allow an adjacent cell for the interaction radius and the last published movement frame.
    if not (x-cell<=pos.x/100<=x+2*cell and z-cell<=pos.z/100<=z+2*cell):
        return pb.StatusCode_BAD_REQ
    shovel = equipped_shovel(session)
    if not shovel:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    success = random.randint(1,1000)<=shovel["success_rate"]
    pool = random.choice(POOLS[treasure.item_id]) if success else 7000010
    prize = draw_reward(db.get_players_info(player,"level")[0],pool)
    if not prize:
        return pb.StatusCode_CFG_NOT_FOUND
    db.del_item_detail(player,req.treasure_id)
    write.grant(prize,rsp.rewards)
    rsp.treasure_id,rsp.success = req.treasure_id,success
    return pb.StatusCode_OK


def dig(session,req,rsp):
    return run(session,req,rsp,settle)
