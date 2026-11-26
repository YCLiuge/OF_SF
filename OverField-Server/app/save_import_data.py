"""Project personal gameplay fields from the backup tool's JSON; this file has no network or game dependencies."""
import json
from pathlib import Path, PurePosixPath
import zipfile


SNAPSHOT_LIMIT=16*1024*1024
ZIP_LIMIT=32*1024*1024
DATA_FORMAT="of-sf-progress/1"
ARRAY_LIMITS={"heroes":100,"quests":1800,"chapters":100,"achievements":14000,"stacks":12000,
              "outfits":3500,"gear":2200}
LEGACY_WEAR=("hat","hair","clothes","ornament","pend_chest","pend_pelvis","pend_left_foot","pend_right_foot")
WEAR=("hat","hair","clothes","ornament","pend_top","pend_chest","pend_pelvis","pend_up_face","pend_down_face",
      "pend_left_hand","pend_right_hand","pend_left_foot","pend_right_foot")


class BackupFormatError(ValueError):
    """Fixed diagnostic appropriate for the local backup picker."""


def documents(snapshot, message):
    for entry in snapshot.get("decoded",{}).values():
        if not isinstance(entry,dict) or entry.get("name")!=message:
            continue
        for frame in entry.get("frames",[]):
            if not isinstance(frame,dict) or frame.get("ok") is not True:
                continue
            data=frame.get("decoded")
            if isinstance(data,dict) and data.get("status",{}).get("v")==1:
                yield data


def integer(value,maximum=2147483647):
    if isinstance(value,dict):
        value=value.get("v")
    return value if type(value) is int and 0<=value<=maximum else 0


def numbers(obj,keys):
    return [integer(obj.get(k)) for k in keys]


def source_vector(value):
    if type(value) is not dict or set(value)-{"x","y","z","decimal_places"}:
        return None
    if value.get("decimal_places",0)!=0:
        return None
    axes=[value.get(k,0) for k in ("x","y","z")]
    return axes if all(type(x) is int and -(2**31)<=x<2**31 for x in axes) else None


def source_location(snapshot,main):
    scene=integer(main.get("scene_id"))
    if not scene:return None
    result={"scene":scene}
    source_player=integer(main.get("player_id"),0xffffffff)
    source_channel=integer(main.get("channel_id"),0xffffffff)
    if not source_player or not source_channel:return result
    for notice in documents(snapshot,"SceneDataNotice"):
        data=notice.get("data",{})
        if data.get("scene_id")!=scene or data.get("channel_id")!=source_channel:continue
        own=[p for p in data.get("players",()) if p.get("player_id")==source_player]
        if len(own)!=1:continue
        lead=own[0].get("team",{}).get("char1",{})
        position,rotation=source_vector(lead.get("pos")),source_vector(lead.get("rot"))
        if position is not None and rotation is not None:
            result["pose"]={"pos":position,"rot":rotation}
        # This device is player progress. The scene also contains other players' public objects.
        if scene==100 and any(a.get("area_id")==101 and integer(a.get("area_state"))==1 for a in data.get("areas",())):
            result["bottle_device_open"]=True
        break
    return result


def projected_identity(snapshot):
    """Read the public label from the account summary and the authenticated main role frames."""
    account = snapshot.get("account")
    account = account if isinstance(account, dict) else {}
    frames = list(documents(snapshot, "PlayerMainDataRsp"))
    source = {}
    ids = [v for v in [account.get("playerLabel"), *(d.get("player_label") for d in frames)] if v is not None]
    if ids:
        parsed = [integer(v, 0xffffffff) for v in ids]
        source["uid"] = parsed[0] if parsed[0] and all(v == parsed[0] for v in parsed) else 0
    names = [v for v in [account.get("playerName"), *(d.get("player_name") for d in frames)] if v]
    if names:
        import unicodedata
        clean = [unicodedata.normalize("NFC", v.strip()) if isinstance(v,str) and len(v)<=64 else "" for v in names]
        valid = len(clean[0]) in range(1,17) and all(v==clean[0] for v in clean)
        if valid or ids:
            source["nickname"] = clean[0] if valid else ""
    return source


def source_signature(mains,owners):
    """Accept only consistent, explicit values from this single authenticated player."""
    from private_chat_text import profile_signature
    if len(owners)!=1:
        return None
    observed=[]
    for source in mains:
        if "sign" not in source or integer(source.get("player_id"),0xffffffff) not in owners:
            continue
        text=profile_signature(source["sign"])
        mask=source.get("hide_value",0)
        if text is None or text!=source["sign"] or type(mask) is not int or not 0<=mask<2**32:
            return None
        observed.append((text,bool(mask&2)))
    if observed and all(entry==observed[0] for entry in observed):
        return {"text":observed[0][0],"hidden":observed[0][1]}
    return None


def source_gather_totals(snapshot, main):
    scene,channel = numbers(main, ("scene_id","channel_id"))
    owner = integer(main.get("player_id"),0xffffffff)
    if not scene or not channel or not owner:
        return []
    totals = {}
    for doc in documents(snapshot,"SceneDataNotice"):
        data = doc.get("data",{})
        if data.get("scene_id") != scene or data.get("channel_id") != channel:
            continue
        if sum(p.get("player_id") == owner for p in data.get("players",())) != 1:
            continue
        for row in data.get("gather_limits",()):
            kind = row.get("gather_type",0)
            kind = kind.get("v") if isinstance(kind,dict) else kind
            lifetime = integer(row.get("gather_num"),0xffffffff)
            if type(kind) is int and 0 <= kind < 6 and lifetime:
                totals[kind] = max(totals.get(kind,0),lifetime)
    return [[scene,kind,count] for kind,count in sorted(totals.items())]


def source_field_progress(snapshot, main):
    scene, channel = numbers(main, ("scene_id", "channel_id"))
    owner = integer(main.get("player_id"), 0xffffffff)
    if not owner or not channel or scene in (0, 100, 9999):
        return None
    devices, challenges, collectibles, goals = {}, {}, {}, {}
    riddles, ambiguous_devices = set(), set()
    for notice in documents(snapshot, "SceneDataNotice"):
        data = notice.get("data", {})
        if (data.get("scene_id") != scene or data.get("channel_id") != channel or
                sum(isinstance(p, dict) and p.get("player_id") == owner for p in data.get("players", ())) != 1):
            ambiguous_devices.update(integer(a.get("area_id")) for a in data.get("areas", ()) if isinstance(a, dict))
            continue
        for area in data.get("areas", ()):
            aid, state, level = numbers(area, ("area_id", "area_state", "level"))
            if aid and state <= 1 and 0 < level <= 255:
                devices[aid] = [aid, state, level]
        for entry in data.get("riddles", ()):
            key, state = numbers(entry, ("riddle_id", "state"))
            if key and state == 1 and entry.get("is_win") is True:
                riddles.add(key)
        for entry in data.get("challenges", ()):
            key, state, elapsed = numbers(entry, ("challenge_id", "state", "use_time"))
            if key and state == 1 and elapsed:
                challenges[key] = min(challenges.get(key, elapsed), elapsed)
        for group in data.get("collections", ()):
            kind = integer(group.get("type"))
            entries = group.get("item_map", {})
            if not 1 <= kind <= 6 or not isinstance(entries, dict):
                continue
            for key, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                item, status = numbers(entry, ("item_id", "status"))
                if item and str(item) == str(key) and status <= 1:
                    collectibles[kind, item] = max(status, collectibles.get((kind, item), 0))
    for reply in documents(snapshot, "AreaAchieveListRsp"):
        aid = integer(reply.get("area_id"))
        if aid in devices and aid not in ambiguous_devices:
            for entry in reply.get("achieves", ()):
                goal, count = numbers(entry, ("achieve_id", "count"))
                if goal and count:
                    goals[aid, goal] = max(count, goals.get((aid, goal), 0))
    if any((devices, challenges, riddles, collectibles, goals)):
        return {"scene":scene, "devices":[devices[k] for k in sorted(devices)], "riddles":sorted(riddles),
                "challenges":[[k, v] for k, v in sorted(challenges.items())],
                "collectibles":[[k[0], k[1], v] for k, v in sorted(collectibles.items())],
                "goals":[[k[0], k[1], v] for k, v in sorted(goals.items())]}


def project(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get("format")!="overfield-snapshot/1":
        raise BackupFormatError("请选择全进度工具导出的 snapshot.json 或完整 ZIP。")
    mains=list(documents(snapshot,"PlayerMainDataRsp"))
    owners={owner for entry in mains if (owner:=integer(entry.get("player_id"),0xffffffff))}
    if len(owners)>1:
        raise BackupFormatError("此文件含有多个角色的主存档。请选择单一角色的原始备份。")
    main=next(iter(mains),None)
    if not main:
        raise BackupFormatError("此备份中没有成功解码的个人主存档。")
    data={"format":DATA_FORMAT,"player":numbers(main,("level","exp","world_level")),
          "heroes":[],"quests":[],"chapters":[],"achievements":[],"stacks":[],"outfits":[],"gear":[]}
    sign=source_signature(mains,owners)
    if sign is not None:data["profile_sign"]=sign
    active_head=integer(main.get("head"))
    if active_head:
        data["head"]=active_head
    from save_import_pets import project as project_pets
    pets=project_pets(snapshot,main)
    if pets:
        data["pets"]=pets
    identity = projected_identity(snapshot)
    if identity:
        data["identity"] = identity
    location=source_location(snapshot,main)
    if location:data["progress_location"]=location
    gathered = source_gather_totals(snapshot,main)
    if gathered:data["gather_totals"]=gathered
    field = source_field_progress(snapshot, main)
    if field:data["field_progress"] = field
    for h in main.get("characters",[]):
        core=numbers(h,("character_id","level","max_level","exp","star"))
        skills=[numbers(s,("skill_id","skill_level")) for s in h.get("character_skill_list",[])]
        presets=[]
        for look in h.get("outfit_presets",[])[:3]:
            hide=look.get("outfit_hide_info",{})
            presets.append([integer(look.get("preset_index")),numbers(look,WEAR),
                            numbers(look,(slot+"_dye_scheme_index" for slot in WEAR)),
                            int(hide.get("hide_orn") is True),int(hide.get("hide_braid") is True)])
        data["heroes"].append([*core,skills,presets,integer(h.get("in_use_outfit_preset_index"),2)])
    quest=main.get("quest_detail",{})
    for ch in quest.get("chapters",[]):
        data["chapters"].append([integer(ch.get("chapter_id")),[integer(i) for i in ch.get("rewarded_story_ids",[])]])
    for q in quest.get("quests",[]):
        if q.get("activity_id"):
            continue
        conditions=[numbers(c,("condition_id","status","progress")) for c in q.get("conditions",[])]
        data["quests"].append([*numbers(q,("quest_id","status","complete_count")),conditions])
    achievements={}
    for msg in ("WorldLevelAchieveListRsp","GetAchieveOneGroupRsp"):
        for d in documents(snapshot,msg):
            group=d.get("current_group_achieve_info",{})
            for obj in d.get("achieves",[])+group.get("achieve_lst",[]):
                key,count=numbers(obj,("achieve_id","count"))
                achievements[key]=max(achievements.get(key,0),count)
    data["achievements"]=[[k,v] for k,v in sorted(achievements.items()) if k and v]
    stacks={}
    # The source can include private equipment instance IDs. Only anonymous catalog data leaves the machine.
    gear={}
    durability={}
    for msg in ("PackNotice","GetAllCharacterEquipRsp","GetWeaponRsp","GetArmorRsp","GetPosterRsp","GetPetRsp"):
        for doc in documents(snapshot,msg):
            for obj in doc.get("items",[]):
                item=obj.get("main_item",{});template=integer(item.get("item_id"))
                stack=item.get("base_item",{})
                if template and integer(stack.get("item_id"))==template:
                    stacks[template]=max(stacks.get(template,0),integer(stack.get("num")))
                for typ,keys in (("weapon",("level","star")),("armor",("level","strength_level")),("poster",("star",))):
                    part=item.get(typ,{})
                    equipment=integer(part.get(typ+"_id"))
                    if equipment and type(part.get("instance_id")) is int:
                        if typ=="weapon" and ("durability" in part or msg=="GetWeaponRsp"):
                            # A complete proto3 weapon omits scalar durability when it is zero.
                            durability[part["instance_id"]]=integer(part.get("durability"),0xffffffff)
                        gear[(typ,part["instance_id"])]=[{"weapon":1,"armor":2,"poster":3}[typ],equipment,*numbers(part,keys)]
    data["stacks"]=[[k,v] for k,v in sorted(stacks.items()) if v]
    ordered=sorted(gear,key=lambda key:(gear[key],key))
    data["gear"]=[gear[key] for key in ordered]
    references={key:index+1 for index,key in enumerate(ordered)}
    data["team"]=numbers(main.get("team",{}),("char1","char2","char3"))
    data["loadouts"]=[]
    fishing_rods=[]
    for actor in main.get("characters",()):
        appearance=actor.get("character_appearance",{})
        rod=appearance.get("fishing_rod_instance_id") if isinstance(appearance,dict) else None
        if type(rod) is int:
            ref=references.get(("weapon",rod))
            if ref and rod in durability:
                fishing_rods.append([integer(actor.get("character_id")),ref,durability[rod]])
        active=integer(actor.get("in_use_equipment_preset_index"),2)
        equipped=[]
        for plan in actor.get("equipment_presets",())[:3]:
            index=integer(plan.get("preset_index"),2)
            slots=[references.get(("weapon",integer(plan.get("weapon"))),0)]+[0]*7
            for kind,start,size,field in (("armor",1,4,"equip_type"),("poster",5,3,"poster_index")):
                for part in plan.get(kind+"s",()):
                    pos=integer(part.get(field))
                    ref=references.get((kind,integer(part.get(kind+"_id"))),0)
                    if pos<size and ref:slots[start+pos]=ref
            if any(slots):equipped.append([index,slots])
        if equipped:data["loadouts"].append([integer(actor.get("character_id")),active,equipped])
    bound={entry[1] for entry in fishing_rods}
    for (kind,instance),index in references.items():
        if (kind=="weapon" and 1006001<=data["gear"][index-1][1]<=1006010
                and index not in bound and instance in durability):
            fishing_rods.append([0,index,durability[instance]])
    if fishing_rods:
        data["fishing_rods"]=fishing_rods
    dye=snapshot.get("customisation",{}).get("dye",{})
    for outfit in dye.get("outfitsData",[]):
        records=[]
        for scheme in outfit.get("schemes",[]):
            if scheme.get("is_un_lock") is True or scheme.get("colors"):
                colors=[numbers(c,("pos","r","g","b")) for c in scheme.get("colors",[])]
                records.append([integer(scheme.get("scheme_index")),colors])
        data["outfits"].append([integer(outfit.get("outfit_id")),records])
    if any(len(data[k])>n for k,n in ARRAY_LIMITS.items()):
        raise BackupFormatError("个人快照条目超出支持的上限。")
    return data


def project_file(path):
    path=Path(path)
    if path.is_dir():
        flattened=path/"snapshot.json"
        if not flattened.is_file():
            matches=list(path.glob("*/snapshot.json"))
            if len(matches)!=1:
                raise BackupFormatError("请选择仅含一份 snapshot.json 的备份目录。")
            flattened=matches[0]
        path=flattened
    if path.name.casefold()=="private-server-import.json":
        path=path.with_name("snapshot.json")
    if not path.is_file():
        raise BackupFormatError("找不到 snapshot.json，请选择全进度备份 ZIP 或导出目录。")
    if path.suffix.casefold()==".zip":
        if path.stat().st_size>ZIP_LIMIT:
            raise BackupFormatError("备份 ZIP 超过 32 MiB。")
        with zipfile.ZipFile(path) as archive:
            members=archive.infolist()
            choices=[m for m in members if not m.is_dir() and PurePosixPath(m.filename.replace(chr(92),"/")).name.casefold()=="snapshot.json"]
            if len(members)>128 or len(choices)!=1 or choices[0].file_size>SNAPSHOT_LIMIT or choices[0].flag_bits & 1:
                raise BackupFormatError("备份 ZIP 须包含一份未加密、大小不超过 16 MiB 的 snapshot.json。")
            with archive.open(choices[0]) as stream:
                raw=stream.read(SNAPSHOT_LIMIT+1)
    else:
        if path.name.casefold()!="snapshot.json":
            raise BackupFormatError("请选择 snapshot.json 或完整全进度备份 ZIP。")
        with path.open("rb") as stream:
            raw=stream.read(SNAPSHOT_LIMIT+1)
    if len(raw)>SNAPSHOT_LIMIT:
        raise BackupFormatError("snapshot.json 超过 16 MiB。")
    try:
        return project(json.loads(raw))
    except (RecursionError,KeyError,TypeError,AttributeError) as exc:
        raise BackupFormatError("备份字段与支持的全进度格式不符。") from exc
