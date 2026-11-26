"""Native pet ownership, capture, release, scene display and warehouse upgrades."""
from collections import Counter
import random

from config import Config
from network.msg_id import MsgId
from private_chat_text import chat_text
from private_crafts import payments, save_pack
from private_dungeons import _table, grant
from private_equipment import load
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


def capacity(pid):
    globals_ = _table("GlobalConfig", "config", "key")
    initial = int(globals_["PetBagOriginal"]["values"][0])
    limit = int(globals_["PetBagMax"]["values"][0])
    row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='pet_bag' AND k='capacity'", (pid,)).fetchone()
    return min(limit, max(initial, int(row[0]))) if row else initial


def owned_pets(pid):
    for (blob,) in db.conn.execute("SELECT item_detail_blob FROM items_s WHERE player_id=? ORDER BY instance_id",(pid,)):
        item = pb.ItemDetail.FromString(blob).main_item
        if item.HasField("pet"):
            yield item.pet


def has_room(pid, incoming):
    remaining = capacity(pid) - incoming
    return remaining >= 0 and not any(index >= remaining for index, _ in enumerate(owned_pets(pid)))


def page(session, req, rsp):
    if req.end_index and req.start_index>req.end_index:
        return pb.StatusCode_BAD_REQ
    total = 0
    for index,pet in enumerate(owned_pets(session.player_id)):
        if index>=req.start_index and (not req.end_index or index<req.end_index):
            rsp.pets.add().CopyFrom(pet)
        total = index+1
    rsp.total_num = total
    rsp.end_index = min(req.start_index,total)+len(rsp.pets)
    return pb.StatusCode_OK


def update_scene(session):
    instance_id, = db.get_players_info(session.player_id, "pet_instance_id")
    pet = load(session.player_id, instance_id, "pet")
    session.scene_player.pet_item_id = pet.main_item.item_id if pet else 0
    session.scene_player.pet_name = pet.main_item.pet.name if pet else ""


def publish(session):
    from private_appearance import broadcast
    update_scene(session)
    broadcast(session, pb.SceneActionType_UPDATA_PET)


def rename(session, req, rsp):
    pid = session.player_id
    item = load(pid, req.pet_instance_id, "pet")
    if item is None:
        return pb.StatusCode_PET_NOT_FOUND
    name = chat_text(req.name)
    if not name or not session.chat_guard.allows(name):
        return pb.StatusCode_BAD_REQ
    try:
        float(name)
    except ValueError:
        pass
    else:
        return pb.StatusCode_BAD_REQ
    item.main_item.pet.name = rsp.name = name
    rsp.pet_instance_id = req.pet_instance_id
    db.set_item_detail(pid, item.SerializeToString(), instance_id=req.pet_instance_id)
    session.send(MsgId.PackNotice, pb.PackNotice(status=pb.StatusCode_OK, items=[item]), 0)
    if db.get_players_info(pid,"pet_instance_id")[0] == req.pet_instance_id:
        publish(session)
    return pb.StatusCode_OK


def change(session, req, rsp):
    instance_id = req.pet_instance_id
    if instance_id and load(session.player_id, instance_id, "pet") is None:
        return pb.StatusCode_PET_NOT_FOUND
    if db.get_players_info(session.player_id,"pet_instance_id")[0] != instance_id:
        db.set_players_info(session.player_id,"pet_instance_id",instance_id)
        publish(session)
    rsp.pet_instance_id = instance_id
    return pb.StatusCode_OK


def capture(session, req, rsp):
    pid = session.player_id
    species = res["PetCaptureMonsters"]["monsters"].get(str(req.monster_id))
    tool = _table("Expression", "catcher", "need_item_i_d").get(req.catcher_id)
    item = _table("Item", "item").get(req.catcher_id, {})
    if not species or not tool or item.get("new_bag_item_tag") != pb.EBagItemTag_Catcher or not 0 < req.hp <= req.max_hp:
        return pb.StatusCode_BAD_REQ
    # The original sends the current region when one exists. A missing optional region is zero.
    if req.catch_area_id and not any(r["area_i_d"] == req.catch_area_id for r in
            _table("Area", "area_data").get(session.scene_id, {}).get("area_data_group", ())):
        return pb.StatusCode_BAD_REQ
    if _table("Scene","scene").get(session.scene_id,{}).get("new_scene_type") != 1:
        return pb.StatusCode_BAD_REQ
    if not has_room(pid, 1):
        return pb.StatusCode_PET_GRID_FULL
    costs, status = payments(pid, {req.catcher_id:tool["need_item_count"]})
    if costs is None:
        return status
    # The national item text guarantees a quantum capture. Other rates use the community model.
    quality = item["quality"]
    if not 1 <= quality <= len(Config.PET_CATCH_RATES):
        return pb.StatusCode_CFG_NOT_FOUND
    rsp.success = req.catcher_id == 200100007 or random.random() < Config.PET_CATCH_RATES[quality - 1]
    if rsp.success:
        new = make_item(species, 1, pid)
        pet = new.main_item.pet
        if db.get_item_detail(pid, instance_id=pet.instance_id):
            max_id, = db.conn.execute("SELECT max(instance_id) FROM items_s WHERE player_id=?", (pid,)).fetchone()
            db.conn.execute("UPDATE instance SET instance_id=? WHERE player_id=?", (max_id,pid))
            pet.instance_id = db.get_instance_id(pid)
        pet.catch_scene_id, pet.catch_area_id = session.scene_id, req.catch_area_id
        db.set_item_detail(pid, new.SerializeToString(), instance_id=pet.instance_id)
        rsp.items.add().CopyFrom(new)
        session.send(MsgId.PackNotice, pb.PackNotice(status=pb.StatusCode_OK, items=[new]), 0)
    save_pack(session, costs)
    return pb.StatusCode_OK


def release(session, req, rsp):
    pid = session.player_id
    ids = req.pet_instance_ids
    if not ids or len(ids) != len(set(ids)) or db.get_players_info(pid,"pet_instance_id")[0] in ids:
        return pb.StatusCode_BAD_REQ
    totals = Counter()
    for key in ids:
        owned = load(pid, key, "pet")
        if owned is None:
            return pb.StatusCode_PET_NOT_FOUND
        species = _table("Pet", "pet").get(owned.main_item.item_id)
        if not species:
            return pb.StatusCode_CFG_NOT_FOUND
        for group in species["release_reward"]:
            options = _table("Pet","release_reward").get(group,{}).get("release_reward_item",())
            if not options:
                return pb.StatusCode_CFG_NOT_FOUND
            # Retail previews the first and last entries as a range, separately for every material.
            chosen = random.choice(options)
            totals[chosen["release_reward_i_d"]] += chosen["release_reward_count"]
    for key in ids:
        db.del_item_detail(pid, key)
    grant(session, totals, rsp.items)
    rsp.pet_instance_ids.extend(ids)
    return pb.StatusCode_OK


def expand(session, req, rsp):
    pid = session.player_id
    item = _table("Item", "item").get(req.item_id, {})
    if item.get("new_bag_item_tag") != pb.EBagItemTag_PetGrid or req.choose_item_id or not req.num:
        return pb.StatusCode_BAD_REQ
    globals_ = _table("GlobalConfig","config","key")
    factor = int(globals_["PetBagExpandRadix"]["values"][0])
    limit = int(globals_["PetBagMax"]["values"][0])
    size = capacity(pid) + req.num*factor
    if size > limit:
        return pb.StatusCode_PET_GRID_FULL
    costs, status = payments(pid,{req.item_id:req.num})
    if costs is None:
        return status
    db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'pet_bag','capacity',?)", (pid,size))
    save_pack(session,costs)
    session.send(MsgId.PetWarehouseLimitNotice, pb.PetWarehouseLimitNotice(status=pb.StatusCode_OK,warehouse_limit=size), 0)
    return pb.StatusCode_OK
