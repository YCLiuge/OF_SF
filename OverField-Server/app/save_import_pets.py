"""Project owned pet records and rebind the equipped companion to a fresh local instance."""
from save_import_data import BackupFormatError, documents, integer


FIELDS = ("item_id", "level", "nature_id", "integrity_id", "star", "catch_scene_id",
          "catch_area_id", "catch_time", "size", "weight")
MAX_PETS = 3000
INITIAL_CAPACITY = 100  # CN GlobalConfig.PetBagOriginal


def project(snapshot, main):
    from private_chat_text import chat_text
    known = {}
    expected = 0
    limit = 0
    def collect(pet):
        if type(pet) is not dict:
            return
        key = integer(pet.get("instance_id"), 0xffffffff)
        if not key or not all(integer(pet.get(k)) for k in ("item_id", "level", "nature_id", "integrity_id")):
            return
        values = {field:integer(pet.get(field), 0xffffffff) for field in FIELDS}
        stats = pet.get("integrity", [])
        if type(stats) is not list or len(stats)>6 or any(type(x) is not dict for x in stats):
            return
        values["integrity"] = [[integer(x.get("attr")), integer(x.get("value"))] for x in stats]
        values["special_color"] = pet.get("special_color") is True
        values["name"] = chat_text(pet.get("name", "")) or ""
        known[key] = values
        if len(known)>MAX_PETS:
            raise BackupFormatError("宠物明细超过 3,000 条。")
    for frame in documents(snapshot,"PackNotice"):
        for item in frame.get("items", ()):
            detail = item.get("main_item") if type(item) is dict else None
            pet = detail.get("pet") if type(detail) is dict else None
            if type(pet) is dict and pet.get("item_id") == detail.get("item_id"):
                collect(pet)
    for frame in documents(snapshot,"GetPetRsp"):
        expected = max(expected, integer(frame.get("total_num"), MAX_PETS))
        for pet in frame.get("pets", ()):
            collect(pet)
    for frame in documents(snapshot,"PetWarehouseLimitNotice"):
        limit = integer(frame.get("warehouse_limit"), MAX_PETS)
    if not known and not expected and limit<=INITIAL_CAPACITY:
        return None
    active = integer(main.get("pet_instance_id"), 0xffffffff)
    return {"total":max(len(known),expected), "capacity":limit,
            "entries":[dict(pet,active=key==active) for key, pet in sorted(known.items())]}


def validate(data):
    from private_chat_text import chat_text
    from private_dungeons import _table
    from save_import_gameplay import number, rows, numbers
    if type(data) is not dict or set(data)!={"total","capacity","entries"}:
        raise ValueError("invalid pet collection")
    total = number(data["total"],MAX_PETS)
    capacity = number(data["capacity"],MAX_PETS)
    records = rows(data["entries"],MAX_PETS)
    if len(records)>total:
        raise ValueError("invalid pet total")
    valid = []
    for pet in records:
        if type(pet) is not dict or set(pet)!=set(FIELDS)|{"integrity","special_color","name","active"}:
            raise ValueError("invalid pet record")
        for field in FIELDS:
            number(pet[field],0xffffffff)
        if type(pet["special_color"]) is not bool or type(pet["active"]) is not bool or type(pet["name"]) is not str:
            raise ValueError("invalid pet state")
        stats = [numbers(pair,2) for pair in rows(pet["integrity"],6)]
        attrs = {a for a,_ in stats}
        if len(attrs)!=len(stats) or any(a>5 for a in attrs):
            raise ValueError("invalid pet attributes")
        if (pet["item_id"] not in _table("Pet","pet") or not pet["level"]
                or pet["nature_id"] not in _table("Pet","nature")
                or pet["integrity_id"] not in _table("Pet","completeness")):
            continue
        valid.append(dict(pet,name=chat_text(pet["name"]) or "",integrity=stats))
    if sum(pet["active"] for pet in valid)>1:
        raise ValueError("multiple equipped pets")
    return {"total":total,"capacity":capacity,"entries":valid}


def restore(player, collection):
    from private_pets import capacity
    from proto import net_pb2 as pb
    from utils import db
    # Imported equipment and historical rows can advance beyond the normal ID counter.
    maximum, = db.conn.execute("SELECT MAX(instance_id) FROM items_s WHERE player_id=?",(player,)).fetchone()
    minimum = (maximum or 0)+1
    for entry in collection["entries"]:
        item = pb.ItemDetail()
        main = item.main_item
        main.item_id, main.item_tag = entry["item_id"], pb.EBagItemTag_Pet
        pet = main.pet
        for key in FIELDS:
            setattr(pet,key,entry[key])
        pet.name,pet.special_color = entry["name"],entry["special_color"]
        pet.instance_id = db.get_instance_id(player,minimum=minimum)
        for attr,value in entry["integrity"]:
            pet.integrity.add(attr=attr,value=value)
        db.set_item_detail(player,item.SerializeToString(),instance_id=pet.instance_id)
        if entry["active"]:
            db.set_players_info(player,"pet_instance_id",pet.instance_id)
    size = max(collection["capacity"],len(collection["entries"]))
    if size>capacity(player):
        db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'pet_bag','capacity',?)",(player,size))
