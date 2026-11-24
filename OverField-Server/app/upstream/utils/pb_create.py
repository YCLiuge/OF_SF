import time
from proto.net_pb2 import (
    Vector3,
    Character,
    SceneTeam,
    EPropertyType,
    SceneCharacterOutfitPreset,
    ItemDetail,
    EBagItemTag,
    StatusCode,
    SceneDataNotice,
    PlayerBriefInfo,
    Quest,
    QuestStatus,
    QuestNotice,
)
from network.msg_id import MsgId
from config import Config
import utils.db as db
from datetime import datetime
from utils.res_loader import res
import random
import server.scene_data as scene_data
import server.notice_sync as notice_sync


def update_birthday(session):
    birthday, hidden = db.get_players_info(session.player_id, "birthday,is_hide_birthday")
    session.scene_player.is_birthday = False
    if not birthday or hidden:
        return
    try:
        birthday_date = datetime.strptime(birthday, "%Y-%m-%d")
    except ValueError:
        return
    today = datetime.now()
    session.scene_player.is_birthday = (today.month, today.day) == (birthday_date.month, birthday_date.day)


def make_ScenePlayer(session):
    # 构造ScenePlayer
    player = session.scene_player
    player.player_id = session.player_id
    player.player_name = session.player_name
    from private_appearance import phone_case
    player.avatar_frame=db.get_players_info(session.player_id,"avatar_frame")[0]
    player.phone_case=phone_case(session.player_id)
    from private_pets import update_scene
    update_scene(session)
    player.ClearField("team")
    player.ClearField("food_buff_ids")
    player.ClearField("global_buff_ids")

    player.food_buff_ids.extend([5013073])  # 食物buff ID
    player.global_buff_ids.extend([481, 491])  # 全局buff ID buff未实现

    char_ids, = db.get_players_info(session.player_id, "team")
    update_birthday(session)

    make_SceneTeam(session.player_id, char_ids, player.team)


def fill_scene_equipment(player_id, character, scene_character):
    from private_equipment import load
    from private_equipment_presets import preset_at, slots
    for field in ("weapon_id", "weapon_star", "inscription_id", "inscription_lv", "armors", "posters"):
        scene_character.ClearField(field)
    preset = preset_at(character, character.in_use_equipment_preset_index)
    if preset is None:
        return
    for kind, _, key in slots(preset):
        item = load(player_id, key, kind)
        if item is None:
            continue
        obj = getattr(item.main_item, kind)
        if kind == "weapon":
            scene_character.weapon_id = obj.weapon_id
            scene_character.weapon_star = obj.star
            inscription = load(player_id, obj.inscription1, "inscription")
            if inscription:
                scene_character.inscription_id = obj.inscription1
                scene_character.inscription_lv = inscription.main_item.inscription.level
        elif kind == "armor":
            scene_character.armors.add(armor_id=obj.armor_id, armor_star=obj.strength_level)
        else:
            scene_character.posters.add(poster_id=obj.poster_id, poster_star=obj.star)


def make_SceneTeam(player_id, char_ids, team=None, *, life_scope=(0,0)):
    if team == None:
        team = SceneTeam()
    if char_ids[0]:
        char1 = team.char1
        char1.char_id = char_ids[0]

        chr = Character()
        chr.ParseFromString(db.get_characters(player_id, char_ids[0])[0])
        make_SceneCharacterOutfitPreset(
            player_id,
            active_outfit(chr),
            char1.outfit_preset,
        )

        char1.character_appearance.CopyFrom(chr.character_appearance)
        char1.char_lv = chr.level
        char1.char_star = chr.star
        char1.char_break_lv = chr.max_level
        fill_scene_equipment(player_id, chr, char1)
        char1.gather_weapon = chr.gather_weapon  # 设置采集武器

        # char1.pos.CopyFrom(Vector3())
        # char1.rot.CopyFrom(Vector3())
        char1.pos.x = 2394
        char1.pos.y = 908
        char1.rot.CopyFrom(Vector3())
    if char_ids[1]:
        char2 = team.char2
        char2.char_id = char_ids[1]

        chr = Character()
        chr.ParseFromString(db.get_characters(player_id, char_ids[1])[0])
        make_SceneCharacterOutfitPreset(
            player_id,
            active_outfit(chr),
            char2.outfit_preset,
        )

        char2.character_appearance.CopyFrom(chr.character_appearance)
        char2.char_lv = chr.level
        char2.char_star = chr.star
        char2.char_break_lv = chr.max_level
        # 从equipment_presets中获取武器ID
        fill_scene_equipment(player_id, chr, char2)
        char2.gather_weapon = chr.gather_weapon  # 设置采集武器

        char2.pos.x = 2394
        char2.pos.y = 908
        char2.rot.CopyFrom(Vector3())
    if char_ids[2]:
        char3 = team.char3
        char3.char_id = char_ids[2]

        chr = Character()
        chr.ParseFromString(db.get_characters(player_id, char_ids[2])[0])
        make_SceneCharacterOutfitPreset(
            player_id,
            active_outfit(chr),
            char3.outfit_preset,
        )

        char3.character_appearance.CopyFrom(chr.character_appearance)
        char3.char_lv = chr.level
        char3.char_star = chr.star
        char3.char_break_lv = chr.max_level
        # 从equipment_presets中获取武器ID
        fill_scene_equipment(player_id, chr, char3)
        char3.gather_weapon = chr.gather_weapon  # 设置采集武器

        char3.pos.x = 2394
        char3.pos.y = 908
        char3.rot.CopyFrom(Vector3())
    from private_character_life import apply
    apply(player_id, team, *life_scope)
    return team


def active_outfit(character):
    if not character.outfit_presets:
        return None
    idx=character.in_use_outfit_preset_index
    return character.outfit_presets[idx if idx<len(character.outfit_presets) else 0]


def make_SceneCharacterOutfitPreset(player_id, outfit, sc=None):
    from private_appearance import WEAR_SLOTS
    if sc is None:
        sc=SceneCharacterOutfitPreset()
    sc.Clear()
    if outfit is None:
        return sc
    sc.hide_info.CopyFrom(outfit.outfit_hide_info)
    for slot in WEAR_SLOTS:
        key=getattr(outfit,slot)
        if not key:
            continue
        raw=db.get_item_detail(player_id,key)
        if not raw:
            continue
        fashion=ItemDetail.FromString(raw).main_item.outfit
        if fashion.outfit_id!=key:
            continue
        setattr(sc,slot,key)
        index=getattr(outfit,slot+"_dye_scheme_index")
        if index>=len(fashion.dye_schemes) or not fashion.dye_schemes[index].is_un_lock:
            index=next((i for i,dye in enumerate(fashion.dye_schemes) if dye.is_un_lock),None)
        if index is not None:
            prefix={"clothes":"cloth","ornament":"orn"}.get(slot,slot)
            getattr(sc,prefix+"_dye_scheme").CopyFrom(fashion.dye_schemes[index])
    return sc


def _property_group(groups, level):
    if level is None:
        return random.randint(1, len(groups))
    return next(i for i, row in enumerate(groups, 1) if row["min_level"] <= level <= row["max_level"])


def make_item(item_id, num=1, player_id=0, item_detail=None, is_all=False, *, equipment_level=None) -> list:
    r = []
    if item_detail == None:
        item_detail = ItemDetail()
    from private_dungeons import _table
    rows = res["Item"]["item"]["datas"] if is_all else (_table("Item","item").get(item_id),)
    for i in rows:
        if i is not None:
            item_detail.extra_quality = i.get("quality", 0)
            match i["new_bag_item_tag"]:
                case EBagItemTag.EBagItemTag_Weapon:  # 武器 tag:2
                    for weapon_i in res["Weapon"]["weapon"]["datas"]:
                        if weapon_i["i_d"] == i["i_d"]:
                            tmp = item_detail.main_item
                            if not weapon_i.get("item_i_d"):
                                continue
                            tmp.item_id = weapon_i["item_i_d"]
                            tmp.item_tag = EBagItemTag.EBagItemTag_Weapon
                            weapon = tmp.weapon
                            weapon.weapon_id = weapon_i["item_i_d"]
                            weapon.instance_id = db.get_instance_id(player_id)
                            f = True
                            for prop in res["Weapon"]["weapon_property"]["datas"]:
                                if weapon_i["weapon_property_i_d"] == prop["i_d"]:
                                    weapon.property_index = _property_group(prop["weapon_property_group_info"], equipment_level)
                                    group_s = prop["weapon_property_group_info"][
                                        weapon.property_index - 1
                                    ]
                                    if not group_s.get("min_attack"):
                                        continue
                                    weapon.attack = int(
                                        random.uniform(
                                            group_s["min_attack"], group_s["max_attack"]
                                        )
                                    )
                                    weapon.damage_balance = int(
                                        random.uniform(
                                            group_s["min_damage_balance"],
                                            group_s["max_damage_balance"],
                                        )
                                        * 100
                                    )
                                    weapon.critical_ratio = int(
                                        random.uniform(
                                            group_s["min_critical_ratio"],
                                            group_s["max_critical_ratio"],
                                        )
                                        * 100
                                    )
                                    weapon.level = equipment_level if equipment_level is not None else random.randint(
                                        group_s["min_level"], group_s["max_level"]
                                    )
                                    weapon.star = 1
                                    f = False
                                    break
                            if f:
                                # 无属性的,鱼竿,载具等
                                tmp.is_new = True
                                item_detail.pack_type = 0
                                weapon.attack = 1
                                weapon.damage_balance = 1
                                weapon.critical_ratio = 1
                                weapon.level = 1
                                weapon.star = 1
                                weapon.durability = 100
                                weapon.property_index = 1
                            if is_all:
                                r.append(item_detail)
                                item_detail = ItemDetail()
                                break
                            return item_detail
                case EBagItemTag.EBagItemTag_Armor:  # 防具 tag:3
                    for armor_i in res["Armor"]["armor"]["datas"]:
                        if armor_i["i_d"] == i["i_d"]:
                            tmp = item_detail.main_item
                            tmp.item_id = armor_i["i_d"]
                            tmp.item_tag = EBagItemTag.EBagItemTag_Armor
                            # tmp.is_new = False
                            armor = tmp.armor
                            armor.armor_id = armor_i["i_d"]
                            armor.instance_id = db.get_instance_id(player_id)
                            for prop in res["Armor"]["armor_property"]["datas"]:
                                if armor_i["armor_property_i_d"] == prop["i_d"]:
                                    armor.property_index = _property_group(prop["armor_property_group_info"], equipment_level)
                                    group_s = prop["armor_property_group_info"][
                                        armor.property_index - 1
                                    ]
                                    prop_group = {
                                        EPropertyType.EPropertyType_ExtHp: [
                                            "min_ext_hp",
                                            "max_ext_hp",
                                        ],
                                        EPropertyType.EPropertyType_MaxHPPercent: [
                                            "min_hp_percent",
                                            "max_hp_percent",
                                        ],
                                        EPropertyType.EPropertyType_ExtAttack: [
                                            "min_ext_attack",
                                            "max_ext_attack",
                                        ],
                                        EPropertyType.EPropertyType_AttackPercent: [
                                            "min_attack_percent",
                                            "max_attack_percent",
                                        ],
                                        EPropertyType.EPropertyType_ExtDefense: [
                                            "min_ext_defense",
                                            "max_ext_defense",
                                        ],
                                        EPropertyType.EPropertyType_DefensePercent: [
                                            "min_defense_percent",
                                            "max_defense_percent",
                                        ],
                                        EPropertyType.EPropertyType_CriticalRatio: [
                                            "min_critical_ratio",
                                            "max_critical_ratio",
                                        ],
                                        EPropertyType.EPropertyType_CriticalDamagePercent: [
                                            "min_critical_damage_percent",
                                            "max_critical_damage_percent",
                                        ],
                                        EPropertyType.EPropertyType_RecoverPercent: [
                                            "min_recover_percent",
                                            "max_recover_percent",
                                        ],
                                    }
                                    armor.main_property_type = random.choice(
                                        list(prop_group.keys())
                                    )
                                    val = random.uniform(
                                        group_s[
                                            prop_group[armor.main_property_type][0]
                                        ],
                                        group_s[
                                            prop_group[armor.main_property_type][1]
                                        ],
                                    )
                                    armor.main_property_val = (
                                        int(val)
                                        if armor.main_property_type
                                        in [
                                            EPropertyType.EPropertyType_ExtHp,
                                            EPropertyType.EPropertyType_ExtAttack,
                                            EPropertyType.EPropertyType_ExtDefense,
                                        ]
                                        else int(val * 100)
                                    )
                                    armor.wearer_id = 0
                                    armor.level = equipment_level if equipment_level is not None else random.randint(
                                        group_s["min_level"], group_s["max_level"]
                                    )
                                    # armor.strength_level = 5
                                    # armor.strength_exp = 0
                                    # armor.property_index = 0
                                    # armor.is_lock = False
                                    break
                            if is_all:
                                r.append(item_detail)
                                item_detail = ItemDetail()
                                break
                            return item_detail

                case EBagItemTag.EBagItemTag_Poster:  # 映像 tag:5
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    poster = tmp.poster
                    poster.poster_id = i["i_d"]
                    poster.instance_id = db.get_instance_id(player_id)
                    poster.star = 1
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail

                case EBagItemTag.EBagItemTag_Fashion:  # 时装 tag:21
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    outfit = tmp.outfit
                    outfit.outfit_id = i["i_d"]
                    dye_scheme = outfit.dye_schemes.add()
                    dye_scheme.is_un_lock = True
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail

                case EBagItemTag.EBagItemTag_Inscription:  # 铭文 tag:17
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    tmp.inscription.inscription_id = i["i_d"]
                    tmp.inscription.level = 1
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail

                case EBagItemTag.EBagItemTag_Card:  # 收藏卡 tag:7
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    tmp.character.character_id = i["i_d"]
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail
                    # 这个好像是多余的角色碎片转换成星辰后，删除角色碎片的通知
                case EBagItemTag.EBagItemTag_TreasureMap:  # 藏宝图 tag:53
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    treasure = tmp.treasure
                    treasure.item_id = i["i_d"]
                    treasure.instance_id = db.get_instance_id(player_id)
                    from private_treasure_dig import assign
                    assign(player_id, treasure)
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail
                case EBagItemTag.EBagItemTag_Pet:  # 宠物 tag:56
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    tmp.is_new = True
                    pet = tmp.pet
                    pet.item_id = i["i_d"]
                    pet.instance_id = db.get_instance_id(player_id)
                    pet.level = 1
                    nature_datas = res["Pet"]["nature"]["datas"]
                    pet.nature_id = random.choice(nature_datas)["i_d"]
                    completeness_datas = res["Pet"]["completeness"]["datas"]
                    pet.integrity_id = random.choice(completeness_datas)["i_d"]
                    pet.catch_scene_id = 1
                    pet.catch_time = int(time.time())
                    pet.size = random.randint(80, 150)
                    pet.weight = random.randint(100000, 300000)
                    attrs = [0, 1, 2, 3, 4, 5]
                    random.shuffle(attrs)
                    num_integrity = random.randint(2, 4)
                    species = next((row for row in res["Pet"]["pet"]["datas"] if row["i_d"] == i["i_d"]), {})
                    for attr in attrs[:num_integrity]:
                        integrity = pet.integrity.add()
                        integrity.attr = attr
                        base_stat = species.get(
                            ("hp", "pow", "str", "int", "dex", "luc")[attr], 50
                        )
                        integrity.value = random.randint(
                            max(1, base_stat // 10), max(1, base_stat // 3)
                        )
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail
                case _:
                    # EBagItemTag.EBagItemTag_Gift:  # 礼包 tag:1
                    # EBagItemTag.EBagItemTag_Fragment:  # 角色碎片 tag:4
                    # EBagItemTag.EBagItemTag_Collection:  # 收藏品 tag:6
                    # EBagItemTag.EBagItemTag_Material:  # 材料 tag:8
                    # EBagItemTag.EBagItemTag_Currency:  # 货币 tag:9
                    # EBagItemTag.EBagItemTag_Food:  # 食物 tag:10
                    # EBagItemTag.EBagItemTag_SpellCard
                    # EBagItemTag.EBagItemTag_Item:  # 普通道具 tag:12
                    # EBagItemTag.EBagItemTag_Fish:  # 鱼产 tag:13
                    # EBagItemTag.EBagItemTag_Recipe
                    # EBagItemTag.EBagItemTag_Baitbox:  # 鱼饵箱 tag:15
                    # EBagItemTag.EBagItemTag_Quest
                    # EBagItemTag.EBagItemTag_StrengthStone:  # 强化石 tag:18
                    # EBagItemTag.EBagItemTag_ExpBook:  # 经验书 能量饮料 tag:19
                    # EBagItemTag.EBagItemTag_Head:  # 头像 tag:20
                    # EBagItemTag.EBagItemTag_UnlockItem:  # 解锁道具 tag:22
                    # EBagItemTag.EBagItemTag_AbilityItem:  # 能力道具 tag:23
                    # EBagItemTag.EBagItemTag_UnlockAbilityItem:  # 解锁能力道具 tag:24
                    # EBagItemTag.EBagItemTag_CharacterBadge:  # 角色徽章 tag:25
                    # EBagItemTag.EBagItemTag_DyeStuff
                    # EBagItemTag.EBagItemTag_PlayerExp
                    # EBagItemTag.EBagItemTag_WorldLevel
                    # EBagItemTag.EBagItemTag_Agentia:  # 特殊道具 女装之魂 蔷薇之心 等 tag:29
                    # EBagItemTag.EBagItemTag_MoonStone:  # 月石 技能材料 tag:30
                    # EBagItemTag.EBagItemTag_Umbrella:  # 伞 tag:31
                    # EBagItemTag.EBagItemTag_Vitality:  # 体力药剂 tag:32
                    # EBagItemTag.EBagItemTag_Badge:  # 头衔 tag:33
                    # EBagItemTag.EBagItemTag_Furniture:  # 家具 tag:34
                    # EBagItemTag.EBagItemTag_Energy:  # 精力药水 tag:35
                    # EBagItemTag.EBagItemTag_ShowWeapon
                    # EBagItemTag.EBagItemTag_ShowArmor
                    # EBagItemTag.EBagItemTag_TeleportKey:  # 采集空间钥匙 tag:38
                    # EBagItemTag.EBagItemTag_WallPaper:  # 壁纸 tag:39
                    # EBagItemTag.EBagItemTag_Expression
                    # EBagItemTag.EBagItemTag_MoonCard:  # 月卡通知也许 未发现实际内容 tag:41
                    # EBagItemTag.EBagItemTag_PhoneCase:  # 手机壁纸 tag:42
                    # EBagItemTag.EBagItemTag_Pendant:  # 挂件 tag:43
                    # EBagItemTag.EBagItemTag_AvatarFrame:  # 头像框 tag:44
                    # EBagItemTag.EBagItemTag_IntimacyGift:  # 亲密度礼物 tag:45
                    # EBagItemTag.EBagItemTag_MusicNote:  # 音乐册 tag:46
                    # EBagItemTag.EBagItemTag_MonthlyCard:  # 月度卡 未知 tag:47
                    # EBagItemTag.EBagItemTag_BattlePassCard:  # 战斗通行证 未知 tag:48
                    # EBagItemTag.EBagItemTag_MonthlyGiftCard:  # 月度礼物卡 tag:49
                    # EBagItemTag.EBagItemTag_BattlePassGiftCard # 战斗通行证礼物卡 tag:50
                    # EBagItemTag.EBagItemTag_SeasonalMiniGamesItem:  # 小游戏道具 tag:51
                    # EBagItemTag.EBagItemTag_Vehicle
                    tmp = item_detail.main_item
                    tmp.item_id = i["i_d"]
                    tmp.item_tag = i["new_bag_item_tag"]
                    tmp.base_item.item_id = i["i_d"]
                    tmp.base_item.num = num
                    if is_all:
                        r.append(item_detail)
                        item_detail = ItemDetail()
                        continue
                    return item_detail
    return r


def make_treasure_box_item(
    player_id,
    world_level,
    num=0,
) -> list:  # TODO 仅生成武器和防具, 未考虑未解锁的武器, 未实现根据世界等级调整概率,
    if not num:
        num = random.randint(2, 7)
    wp_num = random.randint(0, num)
    armor_num = num - wp_num
    items = []
    eligible = set(res["ReleasedEquipment"]["equipment_ids"])
    weapons = [r for r in res["Weapon"]["weapon"]["datas"] if r.get("item_i_d") in eligible]
    armors = [r for r in res["Armor"]["armor"]["datas"] if r.get("item_i_d") in eligible]
    while wp_num > 0 and weapons:
        weapon_i = random.choice(weapons)
        item_detail = ItemDetail()
        item_detail.pack_type = ItemDetail.PackType.PackType_TempStorageArea
        tmp = item_detail.main_item
        if not weapon_i.get("item_i_d"):
            continue
        tmp.item_id = weapon_i["item_i_d"]
        tmp.item_tag = EBagItemTag.EBagItemTag_Weapon
        weapon = tmp.weapon
        weapon.weapon_id = weapon_i["item_i_d"]
        weapon.instance_id = db.get_instance_id(player_id)
        for prop in res["Weapon"]["weapon_property"]["datas"]:
            if weapon_i["weapon_property_i_d"] == prop["i_d"]:
                weapon.property_index = random.randint(
                    1, len(prop["weapon_property_group_info"])
                )
                group_s = prop["weapon_property_group_info"][weapon.property_index - 1]
                if not group_s.get("min_attack"):
                    continue
                weapon.attack = int(
                    random.uniform(group_s["min_attack"], group_s["max_attack"])
                )
                weapon.damage_balance = int(
                    random.uniform(
                        group_s["min_damage_balance"], group_s["max_damage_balance"]
                    )
                )
                weapon.critical_ratio = int(
                    random.uniform(
                        group_s["min_critical_ratio"], group_s["max_critical_ratio"]
                    )
                )
                weapon.level = random.randint(
                    group_s["min_level"], group_s["max_level"]
                )
                weapon.star = 1
                items.append(item_detail)
                wp_num -= 1
                break
    while armor_num > 0 and armors:
        armor_i = random.choice(armors)
        item_detail = ItemDetail()
        tmp = item_detail.main_item
        tmp.item_id = armor_i["i_d"]
        tmp.item_tag = EBagItemTag.EBagItemTag_Armor
        # tmp.is_new = False
        armor = tmp.armor
        armor.armor_id = armor_i["i_d"]
        armor.instance_id = db.get_instance_id(player_id)
        for prop in res["Armor"]["armor_property"]["datas"]:
            if armor_i["armor_property_i_d"] == prop["i_d"]:
                armor.property_index = random.randint(
                    1, len(prop["armor_property_group_info"])
                )
                group_s = prop["armor_property_group_info"][armor.property_index - 1]
                prop_group = {
                    EPropertyType.EPropertyType_ExtHp: ["min_ext_hp", "max_ext_hp"],
                    EPropertyType.EPropertyType_MaxHPPercent: [
                        "min_hp_percent",
                        "max_hp_percent",
                    ],
                    EPropertyType.EPropertyType_ExtAttack: [
                        "min_ext_attack",
                        "max_ext_attack",
                    ],
                    EPropertyType.EPropertyType_AttackPercent: [
                        "min_attack_percent",
                        "max_attack_percent",
                    ],
                    EPropertyType.EPropertyType_ExtDefense: [
                        "min_ext_defense",
                        "max_ext_defense",
                    ],
                    EPropertyType.EPropertyType_DefensePercent: [
                        "min_defense_percent",
                        "max_defense_percent",
                    ],
                    EPropertyType.EPropertyType_CriticalRatio: [
                        "min_critical_ratio",
                        "max_critical_ratio",
                    ],
                    EPropertyType.EPropertyType_CriticalDamagePercent: [
                        "min_critical_damage_percent",
                        "max_critical_damage_percent",
                    ],
                    EPropertyType.EPropertyType_RecoverPercent: [
                        "min_recover_percent",
                        "max_recover_percent",
                    ],
                }
                armor.main_property_type = random.choice(list(prop_group.keys()))
                armor.main_property_val = 0
                random.uniform(
                    group_s[prop_group[armor.main_property_type][0]],
                    group_s[prop_group[armor.main_property_type][1]],
                )
                armor.wearer_id = 0
                armor.level = random.randint(group_s["min_level"], group_s["max_level"])
                # armor.strength_level = 5
                # armor.strength_exp = 0
                # armor.property_index = 0
                # armor.is_lock = False
                items.append(item_detail)
                armor_num -= 1
                break
    return items


def make_SceneDataNotice(session):
    rsp = SceneDataNotice()
    rsp.status = StatusCode.StatusCode_OK
    data = rsp.data
    data.scene_id = session.scene_id
    from private_field_gathering import fill as fill_gather
    fill_gather(session, data)
    for i in db.get_area(session.player_id, session.scene_id):
        data.areas.add().ParseFromString(i)
    pos = session.pos.get(session.scene_id)
    if pos:
        session.scene_player.team.char1.pos.CopyFrom(pos)
    else:
        pos = session.scene_player.team.char1.pos
        rot = session.scene_player.team.char1.rot
        pos.Clear()
        rot.Clear()
        for i in res["ScenesConfigAsset"]["Scenes"]:
            if i["ID"] == session.scene_id:
                pos.x = int(i["Born"][0]["Position"]["x"] * 100)
                pos.y = int(i["Born"][0]["Position"]["y"] * 100)
                pos.z = int(i["Born"][0]["Position"]["z"] * 100)
                rot.y = int(i["Born"][0]["Rotation"]["y"] * 100)
                break
    # Use the pose previously committed by this independent private realm.
    saved = db.db.execute("SELECT pos,rot FROM private_poses WHERE player_id=? AND scene=?",
                          (session.player_id, session.scene_id)).fetchone()
    if saved:
        session.scene_player.team.char1.pos.ParseFromString(saved[0])
        session.scene_player.team.char1.rot.ParseFromString(saved[1])
    data.players.add().CopyFrom(session.scene_player)
    from private_chairs import fill
    fill(session, data)
    from private_collection_rewards import fill_scene as fill_collections
    fill_collections(session, data)
    for i in db.get_challenge(session.player_id, session.scene_id):
        data.challenges.add().ParseFromString(i)
    from private_flag_battle import fill as fill_flag_battles
    fill_flag_battles(session, data)
    from private_room_decor import fill as fill_room_decor
    fill_room_decor(session, data)
    from private_field_activities import fill as fill_field_activities
    fill_field_activities(session,data)
    from private_mp_rooms import scene_beacons
    scene_beacons(session, data)
    session.campfires.snapshot(session, data)
    from private_channels import fill_garden
    fill_garden(session,data.scene_garden_data)
    from private_garden_residents import snapshot as fill_residents
    fill_residents(session,data)
    for m in res["Flag"]["flag"]["datas"]:
        if m["scene_i_d"] == session.scene_id and m["icon_i_d"] in [
            1,
            3,
            7,
        ]:  # 战斗任务，美丽景点，副本
            rsp.data.flag_ids.append(m["i_d"])
    for peer in scene_data.room_members(session):
        if peer is not session:
            data.players.add().CopyFrom(peer.scene_player)
    from private_vehicle_seats import fill as fill_vehicles
    fill_vehicles(session, data)
    data.channel_id = session.channel_id
    from private_plaza import game_seconds, weather
    from private_boxes import fill_scene
    now = game_seconds()
    data.tod_time = now % 86400
    data.weather_type = weather(now // 3600)
    fill_scene(session, data)
    for blob in db.get_dungeon(session.player_id).values():
        data.dungeons.add().ParseFromString(blob)
    data.channel_label = session.channel_id
    return rsp


def make_PlayerBriefInfo(player_id, info=None, *, online=None):
    if info is None:
        info = PlayerBriefInfo()
    info.is_online = (scene_data.online_session(player_id) is not None) if online is None else online
    (
        info.player_id,
        info.nick_name,
        info.level,
        info.head,
        info.last_login_time,
        info.sex,
        info.phone_background,
        info.sign,
        info.guild_name,
        info.team_leader_badge,
        info.character_id,
        info.create_time,
        info.player_label,
        info.garden_like_num,
        info.account_type,
        info.birthday,
        info.hide_value,
        info.avatar_frame,
    ) = db.get_players_info(
        player_id,
        "player_id,player_name,level,head,last_login_time,sex,phone_background,sign,guild_name,team_leader_badge,character_id,create_time,player_id,garden_like_num,account_type,birthday,hide_value,avatar_frame",
    )
    from private_appearance import phone_case
    info.phone_case=phone_case(player_id)
    from proto import net_pb2 as pb
    if info.hide_value & pb.HideType_SIGN_Type:
        info.ClearField("sign")
    if info.hide_value & pb.HideType_Account_Type:
        info.ClearField("account_type")
    if db.get_players_info(player_id,"is_hide_birthday")[0]:
        info.ClearField("birthday")
    return info


def make_Quest(player_id, quest_id, q=None):
    from private_dungeons import _table
    row = _table("Quest", "quest").get(quest_id)
    if row is None:
        return None
    if q is None:
        q = Quest()
    q.quest_id = quest_id
    q.status = QuestStatus.QuestStatus_InProgress
    q.bonus_times = 1
    group = _table("Quest", "condition_set_group").get(row["condition_set_group_i_d"])
    if group is None:
        return None
    for phase in group["quest_condition_set"]:
        for key in phase["achieve_condition_i_d"]:
            q.conditions.add(condition_id=key, status=QuestStatus.QuestStatus_InProgress)
    return q


_quest_event_index = None


def quest_event_index(quests, groups):
    """Index the authored event routes once per loaded pair of resource tables."""
    global _quest_event_index
    if _quest_event_index is not None:
        old_quests, old_groups, by_condition, successor = _quest_event_index
        if old_quests is quests and old_groups is groups:
            return by_condition, successor

    by_condition, successor, previous = {}, {}, {}
    for position, (qid, row) in enumerate(quests.items()):
        group = row.get("quest_group")
        if group in previous:
            successor[previous[group]] = qid
        previous[group] = qid
        phases = groups.get(row.get("condition_set_group_i_d"), {}).get("quest_condition_set", ())
        entry = (position, qid)
        for phase in phases:
            for key in phase["achieve_condition_i_d"]:
                by_condition.setdefault(key, []).append(entry)
    _quest_event_index = quests, groups, by_condition, successor
    return by_condition, successor


def make_QuestNotice(session, conds, qn=None):
    """Apply one native event to the earliest unfinished condition group of each active objective."""
    from private_dungeons import _table
    from private_first_quest import save, saved
    hits = set(conds)
    if not hits:
        return
    notice = qn if qn is not None else QuestNotice()
    quests = _table("Quest", "quest")
    groups = _table("Quest", "condition_set_group")
    achieves = _table("Achieve", "achieve")
    by_condition, successor = quest_event_index(quests, groups)
    # A single event can hit several phases or quests. Snapshot in authored order
    # before advancing anything, so newly unlocked steps need a later interaction.
    matches = {entry for key in hits for entry in by_condition.get(key, ())}
    candidates = []
    for _, qid in sorted(matches):
        current = saved(session.player_id, qid)
        if current and current.status == QuestStatus.QuestStatus_InProgress:
            row = quests[qid]
            phases = groups[row["condition_set_group_i_d"]]["quest_condition_set"]
            candidates.append((row, phases, current))
    for row, phases, current in candidates:
        conditions = {c.condition_id: c for c in current.conditions}
        pending = next((phase["achieve_condition_i_d"] for phase in phases
                        if any(key in conditions and conditions[key].status != QuestStatus.QuestStatus_Finish
                               for key in phase["achieve_condition_i_d"])), ())
        matched = [conditions[key] for key in pending if key in hits and key in conditions
                   and conditions[key].status == QuestStatus.QuestStatus_InProgress]
        if not matched:
            continue
        for condition in matched:
            target = achieves.get(condition.condition_id, {}).get("count_param", 1)
            condition.progress = min(condition.progress + 1, target)
            if condition.progress == target:
                condition.status = QuestStatus.QuestStatus_Finish
        finished = all(c.status == QuestStatus.QuestStatus_Finish for c in current.conditions)
        if finished:
            from private_extra_story_rewards import manual_end
            if row.get("new_type")==7 and row.get("reward")==2000600:
                from private_bless_tasks import set_reward_phase
                set_reward_phase(current,QuestStatus.QuestStatus_Completed)
            elif manual_end(row):
                current.status = QuestStatus.QuestStatus_Completed
            else:
                current.status = QuestStatus.QuestStatus_Finish
                current.complete_count = max(1, current.complete_count)
        if finished and row.get("reward") and row.get("is_immed_reward"):
            from private_field_story import step_reward
            step_reward(session, row)
        save(session, current)
        notice.quests.add().CopyFrom(current)
        if finished and row.get("new_type")==8:
            from private_bless_tasks import record as record_prayer
            record_prayer(session,84)
        if finished:
            next_id = successor.get(current.quest_id)
            if next_id is not None and saved(session.player_id, next_id) is None:
                following = make_Quest(session.player_id, next_id)
                save(session, following)
                from private_flag_battle import prior_completion
                completed = prior_completion(session,following)
                if completed is None:
                    notice.quests.add().CopyFrom(following)
                else:
                    make_QuestNotice(session,(completed,),notice)
    if notice.quests:
        notice.status = StatusCode.StatusCode_OK
        return notice


def make_Achieve(session, scene):
    """Only an actual scene entry can satisfy a type-39 story condition."""
    if Config.SKIP_QUESTS:
        return
    from private_story_entry import CONDITIONS as entry_conditions
    from private_dungeons import _table
    goals = [key for key, row in _table("Achieve","achieve").items()
             if row.get("new_achieve_condition") == 39 and row.get("param") == [scene]
             and key not in entry_conditions]
    notice = make_QuestNotice(session, goals)
    if notice:
        session.send(MsgId.QuestNotice, notice, 0)
