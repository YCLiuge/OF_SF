"""Outfit palettes, rolls and saved colors for the native coloring and scheme panels."""
from functools import partial
import random

from private_appearance import WEAR_SLOTS, publish
from utils.res_loader import res
from private_dye_palette import colors
from private_gameplay_transaction import run, Rejected
from private_newcomer import record
from proto import net_pb2 as pb
from utils import db


def global_value(key):
    return res.index("GlobalConfig", "config", "key")[key]["values"][0]


def price(pending, key):
    item, count = map(int, global_value(key).split(":"))
    pending.balances.change(item, -count)


def state(player, key, default=None):
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='fashion_dye' AND k=?",
                          (player, key)).fetchone()
    return row[0] if row else default


def write(player, key, value):
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'fashion_dye',?,?)", (player, key, value))


def fill_login(player, main):
    main.total_guarantee_dye_num = state(player, "misses", 0)
    main.un_save_outfit_dye_scheme.ParseFromString(state(player, "result", b""))


def outfit(player, key):
    raw = db.get_item_detail(player, key)
    value = pb.ItemDetail.FromString(raw) if raw else None
    if (not value or not value.main_item.HasField("outfit") or value.main_item.outfit.outfit_id != key
            or not res.index("Fashion", "fashion_info").get(key, {}).get("asset_full_name")):
        raise Rejected(pb.StatusCode_NOT_DYE)
    return value


def store_outfit(player, item, pending):
    db.set_item_detail(player, item.SerializeToString(), item.main_item.item_id)
    pending.pack.items.add().CopyFrom(item)


def unlock(session, req, rsp, pending, supplement=False):
    item = outfit(session.player_id, req.outfit_id)
    schemes = item.main_item.outfit.dye_schemes
    if req.index >= int(global_value("Fashion_ColoringPlan_Count")):
        return pb.StatusCode_BAD_REQ
    if req.index < len(schemes) and schemes[req.index].is_un_lock:
        return pb.StatusCode_OUTFIT_SCHEME_INDEX_HAS_UNLOCKED
    if supplement:
        currency, cost = map(int, global_value("Fashion_ColoringPlan_Price").split(":"))
        available = min(cost, pending.balances.change(currency, 0).base_item.num)
        pending.balances.change(currency, -available)
        if cost > available:
            pending.balances.change(108, available - cost)
    else:
        price(pending, "Fashion_ColoringPlan_Price")
    while len(schemes) <= req.index:
        schemes.add(scheme_index=len(schemes))
    schemes[req.index].is_un_lock = True
    store_outfit(session.player_id, item, pending)
    return pb.StatusCode_OK


def select(session, req, rsp, pending):
    player = session.player_id
    outfit(player, req.outfit_id)
    if req.colorant_item_id:
        if req.colorant_item_id not in res.index("DyeStuff", "dye_data"):
            return pb.StatusCode_CFG_NOT_FOUND
        raw = db.get_item_detail(player, req.colorant_item_id)
        if not raw or not pb.ItemDetail.FromString(raw).main_item.base_item.num:
            return pb.StatusCode_ITEM_NOT_ENOUGH
    palette = rsp.param
    previous = getattr(session, "dye_palette", None)
    if previous and previous[:2] == (req.outfit_id, req.colorant_item_id):
        palette.ParseFromString(previous[2])
        return pb.StatusCode_OK
    palette.picture_id = random.randint(1, 4)
    for _ in range(16):
        palette.params.extend((random.uniform(.2, .8), random.uniform(.2, .8),
                               random.uniform(.2, .8), random.choice((-1, 1)) * random.uniform(.2, .8)))
    pending.after_commit.append((setattr, (session, "dye_palette", (req.outfit_id, req.colorant_item_id,
                                                                   palette.SerializeToString()))))
    return pb.StatusCode_OK


def selected(session, req):
    palette = getattr(session, "dye_palette", None)
    if not palette or palette[0] != req.outfit_id or not palette[1] or not 0 <= req.uvy <= 1:
        raise Rejected(pb.StatusCode_BAD_REQ)
    return res.index("DyeStuff", "dye_data")[palette[1]], pb.OutfitDyeParam.FromString(palette[2])


def preview(session, req, rsp, pending):
    row, palette = selected(session, req)
    if row.get("new_dye_type"):
        return pb.StatusCode_RANDOM_DYE_STUFF_NOT_RANDOM_COLOR
    roll = random.choice(colors(session.dye_texture_dir, palette, req.uvy))
    rsp.outfit_id = req.outfit_id
    rsp.pos_color.red, rsp.pos_color.green, rsp.pos_color.blue = roll
    return pb.StatusCode_OK


def choose_color(session, req, palette, misses):
    candidates = colors(session.dye_texture_dir, palette, req.uvy)
    target = req.add_rate_color_index
    threshold = int(global_value("Fashion_DyeStuff_Num"))
    if not target:
        return random.choice(candidates), misses
    # The selected swatch has an increased chance until its guarantee is full.
    # After five unsuccessful rolls the next attempt yields the selected swatch.
    preferred = candidates[target - 1]
    chance = 100 // len(candidates) + int(global_value("Fashion_dye_ProbabilityAdd"))
    if misses >= threshold or random.randrange(100) < chance:
        return preferred, 0
    others = candidates[:target-1] + candidates[target:]
    rolled = random.choice(others)
    return rolled, 0 if rolled == preferred else misses + 1


def draw(session, req, rsp, pending):
    player = session.player_id
    schemes = outfit(player, req.outfit_id).main_item.outfit.dye_schemes
    if req.scheme_index >= len(schemes) or not schemes[req.scheme_index].is_un_lock:
        return pb.StatusCode_OUTFIT_SCHEME_INDEX_NOT_UNLOCK
    if req.pos >= 5 or req.add_rate_color_index > 5 or state(player, "result"):
        return pb.StatusCode_BAD_REQ
    row, palette = selected(session, req)
    misses = state(player, "misses", 0)
    custom = req.HasField("specify_color")
    if custom:
        rgb = (req.specify_color.red, req.specify_color.green, req.specify_color.blue)
        if row.get("new_dye_type") or req.add_rate_color_index or max(rgb) > 255:
            return pb.StatusCode_BAD_REQ
        price(pending, "Fashion_Customize_Color_Price")
    elif row.get("new_dye_type"):
        rgb = tuple(row.get("color_" + c, 0) for c in "rgb")
    else:
        rgb, misses = choose_color(session, req, palette, misses)
    pending.balances.change(row["i_d"], -1)
    result = pb.UnSaveOutfitDyeScheme(outfit_id=req.outfit_id, scheme_index=req.scheme_index,
                                     pos=req.pos, dye_item_id=row["i_d"])
    color = result.color
    color.pos = req.pos
    color.red, color.green, color.blue = rgb
    write(player, "result", result.SerializeToString())
    write(player, "misses", misses)
    record(session, 164)
    rsp.outfit_id = req.outfit_id
    rsp.pos_color.CopyFrom(color)
    rsp.total_guarantee_dye_num = misses
    return pb.StatusCode_OK


def worn(player, key, scheme):
    for char_id in db.get_players_info(player, "team")[0] or ():
        if not char_id:
            continue
        rows = db.get_characters(player, char_id)
        if not rows:
            continue
        character = pb.Character.FromString(rows[0])
        if character.in_use_outfit_preset_index >= len(character.outfit_presets):
            continue
        preset = character.outfit_presets[character.in_use_outfit_preset_index]
        if any(getattr(preset, slot) == key and getattr(preset, slot + "_dye_scheme_index") == scheme
               for slot in WEAR_SLOTS):
            return True
    return False


def save(session, req, rsp, pending):
    player = session.player_id
    result = pb.UnSaveOutfitDyeScheme.FromString(state(player, "result", b""))
    if not result.outfit_id or (result.outfit_id, result.scheme_index, result.pos) != (
            req.outfit_id, req.scheme_index, req.pos):
        return pb.StatusCode_NOT_DYE
    if req.is_save_dye_result:
        item = outfit(player, req.outfit_id)
        schemes = item.main_item.outfit.dye_schemes
        if req.scheme_index >= len(schemes) or not schemes[req.scheme_index].is_un_lock:
            return pb.StatusCode_OUTFIT_SCHEME_INDEX_NOT_UNLOCK
        saved = schemes[req.scheme_index].colors
        color = next((entry for entry in saved if entry.pos == req.pos), None)
        if color is None:
            color = saved.add()
        color.CopyFrom(result.color)
        store_outfit(player, item, pending)
        rsp.items.add().CopyFrom(item)
        rsp.pos_color.CopyFrom(color)
        if worn(player, req.outfit_id, req.scheme_index):
            pending.after_commit.append((publish, (session, pb.SceneActionType_UPDATE_FASHION)))
    rsp.scheme_index = req.scheme_index
    rsp.is_save_dye_result = req.is_save_dye_result
    db.conn.execute("DELETE FROM player_kv WHERE player_id=? AND ns='fashion_dye' AND k='result'", (player,))
    return pb.StatusCode_OK


def generic(session, req, rsp, pending):
    rsp.generic_msg_id = req.generic_msg_id
    if req.generic_msg_id == 2:
        from private_dungeon_sweep import preference
        return preference(session, req, rsp)
    if req.generic_msg_id not in (5, 7):
        return pb.StatusCode_FAIL
    if len(req.params) != 1 or req.param_map or req.params[0].param_type not in (
            pb.COMMON_PARAM_TYPE_INT, pb.COMMON_PARAM_TYPE_NONE):
        return pb.StatusCode_BAD_REQ
    key = req.params[0].int_value
    if not 0 < key <= 0xffffffff:
        return pb.StatusCode_BAD_REQ
    rsp.params.extend(req.params)
    if req.generic_msg_id == 7:
        palette = getattr(session, "dye_palette", None)
        if not palette or palette[0] != key or not palette[1] or res.index("DyeStuff", "dye_data")[palette[1]].get("new_dye_type"):
            return pb.StatusCode_RANDOM_DYE_STUFF_NOT_RANDOM_COLOR
        # The success callback reselects the same dye and reads a new palette.
        pending.after_commit.append((setattr, (session, "dye_palette", None)))
        return pb.StatusCode_OK
    schemes = outfit(session.player_id, key).main_item.outfit.dye_schemes
    index = next((i for i, scheme in enumerate(schemes) if not scheme.is_un_lock), len(schemes))
    return unlock(session, pb.OutfitDyeUnlockIndexReq(outfit_id=key, index=index), rsp, pending, supplement=True)


def atomic(operation, session, request, response):
    return run(session, request, response, operation)


unlock_scheme = partial(atomic, unlock)
select_colorant = partial(atomic, select)
preview_color = partial(atomic, preview)
draw_color = partial(atomic, draw)
save_color = partial(atomic, save)

generic_dye = partial(atomic, generic)
