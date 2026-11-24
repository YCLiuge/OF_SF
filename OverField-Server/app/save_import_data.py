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


def project(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get("format")!="overfield-snapshot/1":
        raise BackupFormatError("请选择全进度工具导出的 snapshot.json 或完整 ZIP。")
    main=next(documents(snapshot,"PlayerMainDataRsp"),None)
    if not main:
        raise BackupFormatError("此备份中没有成功解码的个人主存档。")
    data={"format":DATA_FORMAT,"player":numbers(main,("level","exp","world_level")),
          "heroes":[],"quests":[],"chapters":[],"achievements":[],"stacks":[],"outfits":[],"gear":[]}
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
                        gear[(typ,part["instance_id"])]=[{"weapon":1,"armor":2,"poster":3}[typ],equipment,*numbers(part,keys)]
    data["stacks"]=[[k,v] for k,v in sorted(stacks.items()) if v]
    ordered=sorted(gear,key=lambda key:(gear[key],key))
    data["gear"]=[gear[key] for key in ordered]
    references={key:index+1 for index,key in enumerate(ordered)}
    data["team"]=numbers(main.get("team",{}),("char1","char2","char3"))
    data["loadouts"]=[]
    for actor in main.get("characters",()):
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
