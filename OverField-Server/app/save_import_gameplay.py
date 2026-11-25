"""Validate the portable public-catalog projection and restore native gameplay rows."""
from collections import defaultdict
from private_dungeons import _table
from private_story_catalog import catalog as story_catalog
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item, make_Quest
from save_import_data import ARRAY_LIMITS, DATA_FORMAT, LEGACY_WEAR, WEAR

LABELS={"heroes":"探索员","quests":"剧情任务","chapters":"章节领奖记录","achievements":"个人成就",
        "stacks":"背包物品","outfits":"服饰","gear":"装备"}
# Only ordinary stored inventory. Currency, gift/payment products and account entitlements are never portable.
BAG_TAGS={4,6,7,8,10,13,14,15,18,19,20,25,26,30,31,33,34,39,40,42,43,44,45,46,52,54}
ACHIEVE_EVENTS={6,36,38,74,75,76,82,124,129,131}


def rows(value,limit):
    if type(value) is not list or len(value)>limit:
        raise ValueError("invalid collection")
    return value


def row(value,size):
    if type(value) is not list or len(value)!=size:
        raise ValueError("invalid row")
    return value


def number(value,maximum=2147483647):
    if type(value) is not int or not 0<=value<=maximum:
        raise ValueError("invalid number")
    return value


def numbers(value,size):
    return [number(v) for v in row(value,size)]


def looks_for_actor(looks):
    normalized=[]
    slots=set()
    for position,look in enumerate(rows(looks,3)):
        if type(look) is list and len(look)==len(LEGACY_WEAR):
            old=dict(zip(LEGACY_WEAR,numbers(look,len(LEGACY_WEAR))))
            index,parts,schemes,hide_orn,hide_braid=position,[old.get(name,0) for name in WEAR],[0]*len(WEAR),0,0
        else:
            index,parts,schemes,hide_orn,hide_braid=row(look,5)
            number(index,2);number(hide_orn,1);number(hide_braid,1)
            parts=numbers(parts,len(WEAR));schemes=numbers(schemes,len(WEAR))
        if index in slots:raise ValueError("duplicate outfit preset")
        slots.add(index)
        normalized.append([index,parts,schemes,hide_orn,hide_braid])
    return normalized


def validate(data):
    from private_entitlements import CURRENCY_IDS
    if type(data) is not dict or set(data)-{"team","loadouts","progress_location","head","gather_totals","pets","field_progress"}!={"format","player",*ARRAY_LIMITS} or data["format"]!=DATA_FORMAT:
        raise ValueError("invalid data format")
    for key,limit in ARRAY_LIMITS.items():
        rows(data[key],limit)
    out={key:[] for key in ARRAY_LIMITS}
    level,xp,world=numbers(data["player"],3)
    player_levels=_table("Level","player_level")
    if level not in player_levels or world not in {0,*_table("Level","world_level")}:
        raise ValueError("unknown level")
    out["player"]=[level,min(xp,max(0,player_levels[level].get("need_exp",0)-1)) if level<max(player_levels) else min(xp,100000),world]
    if "head" in data:
        head=number(data["head"])
        if head in _table("Head","head","head_i_d"):
            out["head"]=head
    table=_table("Item","item")
    for entry in data["stacks"]:
        key,count=numbers(entry,2)
        if key not in CURRENCY_IDS and table.get(key,{}).get("new_bag_item_tag") in BAG_TAGS and count:
            out["stacks"].append([key,min(count,99999)])
    fashions=_table("Fashion","fashion_info")
    from private_outfit_dye import global_value
    max_schemes=int(global_value("Fashion_ColoringPlan_Count"))
    for entry in data["outfits"]:
        key,schemes=row(entry,2);number(key)
        clean={}
        for s in rows(schemes,16):
            index,colors=row(s,2);number(index)
            colors_by_pos={}
            for c in rows(colors,64):
                pos,r,g,b=numbers(c,4)
                if pos<64 and max(r,g,b)<=255:
                    colors_by_pos[pos]=[pos,r,g,b]
            if index<max_schemes:
                clean[index]=[index,list(colors_by_pos.values())]
        if fashions.get(key,{}).get("asset_full_name"):
            out["outfits"].append([key,list(clean.values())])
    definitions=_table("Character","character")
    for entry in data["heroes"]:
        key,lv,cap,exp,star,skills,presets,active=row(entry,8)
        for num in (key,lv,cap,exp,star,active):number(num)
        skills=[numbers(s,2) for s in rows(skills,16)]
        presets=looks_for_actor(presets)
        cfg=definitions.get(key)
        if not cfg or not cfg.get("default_weapon_i_d"):continue
        rules=_table("Character","level_rule").get(cfg.get("rule_i_d"),{}).get("level_rule_info",())
        caps={20,*[v.get("top_max_level",20) for v in rules]}
        cap=max([c for c in caps if c<=max(20,cap)]);lv=max(1,min(lv,cap))
        need=next((r.get("need_exp",0) for r in _table("Character","level")[cfg["growth_level_i_d"]]["level_info"] if r["level"]==lv),0)
        stars=_table("Character","character_star").get(cfg.get("star_effect_i_d"),{}).get("star_info",())
        out["heroes"].append([key,lv,cap,min(exp,max(0,need-1)),min(star,max((s.get("star",0) for s in stars),default=0)),
                              skills,presets,min(active,2)])
    groups=_table("Quest","condition_set_group")
    quests=_table("Quest","quest")
    legal={step for _,_,steps in story_catalog().values() for step in steps}
    condition_sets={}
    inferred=defaultdict(list)
    from save_import_quests import HISTORY, activity_quests, classify
    active_events=activity_quests()
    supplementary={}
    ambiguous=set()
    for qid in legal:
        phases=groups.get(quests[qid].get("condition_set_group_i_d"),{}).get("quest_condition_set",())
        ids=frozenset(k for p in phases for k in p.get("achieve_condition_i_d",()))
        condition_sets[qid]=ids
        if ids:inferred[ids].append(qid)
    achieve=_table("Achieve","achieve")
    for entry in data["quests"]:
        key,state,count,conditions=row(entry,4)
        for v in (key,state,count):number(v)
        cs=[numbers(c,3) for c in rows(conditions,64)]
        ids=frozenset(c[0] for c in cs)
        if not key and len(inferred.get(ids,()))==1:key=inferred[ids][0]
        if not key or key not in quests or state not in (1,2,3) or len(ids)!=len(cs):continue
        if key not in condition_sets:
            phases=groups.get(quests[key].get("condition_set_group_i_d"),{}).get("quest_condition_set",())
            condition_sets[key]=frozenset(cid for phase in phases for cid in phase.get("achieve_condition_i_d",()))
        if ids!=condition_sets[key]:continue
        if any(st>3 for _,st,_ in cs):continue
        if key not in legal:
            if not cs or any(cid not in achieve for cid,_,_ in cs):continue
            kind=classify(quests[key],active_events)
            record=[key,state,count,cs]
            # Retail exports can retain several past completions even on a template whose
            # current limit is one. A nonterminal replay needs its original reset context.
            if kind=="persistent" and count>1 and state!=pb.QuestStatus_Finish:kind="history"
            if kind=="persistent":
                record[3]=[[cid,st,min(n,achieve[cid].get("count_param",1))] for cid,st,n in cs]
            if key in supplementary and supplementary[key]!=(kind,record):ambiguous.add(key)
            else:supplementary[key]=(kind,record)
            continue
        cs=[[cid,st,min(n,achieve.get(cid,{}).get("count_param",1))] for cid,st,n in cs]
        out["quests"].append([key,state,min(count,max(1,quests[key].get("limit_num",1))),cs])
    chapters=_table("Story","story_chapter")
    for entry in data["chapters"]:
        key,stories=row(entry,2);number(key)
        stories=[number(i) for i in rows(stories,256)]
        if key in chapters:
            out["chapters"].append([key,sorted(set(stories)&set(chapters[key]["story_list"]))])
    for entry in data["achievements"]:
        key,count=numbers(entry,2)
        cfg=achieve.get(key,{})
        if cfg.get("new_achieve_condition") in ACHIEVE_EVENTS and cfg.get("count_param") and count:
            out["achievements"].append([key,min(count,cfg["count_param"])])
    gear_index={}
    for original_index,entry in enumerate(data["gear"],1):
        if type(entry) is not list or not entry or type(entry[0]) is not int or entry[0] not in (1,2,3):
            raise ValueError("unknown gear type")
        kind,key,*stats=numbers(entry,3 if entry[0]==3 else 4)
        resource=("Weapon","Armor","Poster")[kind-1]
        if key not in _table(resource,resource.lower()) or table.get(key,{}).get("new_bag_item_tag")!=(2,3,5)[kind-1]:continue
        if kind==3:stats=[max(1,min(stats[0],5))]
        else:
            group=resource.lower()+"_property"
            cfg=_table(resource,resource.lower())[key]
            ranges=_table(resource,group).get(cfg.get(group+"_i_d"),{}).get(group+"_group_info",())
            if not ranges or not cfg.get("item_i_d"):continue
            level=max(1,min(stats[0],100))
            if not any(r.get("min_level",0)<=level<=r.get("max_level",0) for r in ranges):continue
            stats=[level,min(stats[1],5 if kind==1 else 20)]
        gear_index[original_index]=len(out["gear"])+1
        out["gear"].append([kind,key,*stats])
    for key in ARRAY_LIMITS:
        if key!="gear":out[key]=list({entry[0]:entry for entry in out[key]}.values())
    actors={a[0] for a in out["heroes"]}
    team=numbers(data.get("team",[0,0,0]),3)
    members=[i for i in team if i]
    out["team"]=team if members and team[0] and set(members)<=actors and len(members)==len(set(members)) else []
    out["loadouts"]=[]
    equipped=set()
    for entry in rows(data.get("loadouts",[]),ARRAY_LIMITS["heroes"]):
        actor,active,presets=row(entry,3);number(actor);number(active)
        chosen={}
        for entry in rows(presets,3):
            index,slots=row(entry,2);number(index);values=numbers(slots,8)
            slots=[gear_index.get(j,0) for j in values]
            keys=[k for k in slots if k]
            if (index>2 or not keys or len(keys)!=len(set(keys)) or
                    any(out["gear"][j-1][0]!=(1 if n==0 else 2 if n<5 else 3) for n,j in enumerate(slots) if j)):continue
            compatible=True
            for n,j in enumerate(slots[:5]):
                if not j:continue
                kind,key,*_=out["gear"][j-1];resource=("Weapon","Armor")[kind-1]
                rule=_table(resource,resource.lower())[key]
                if (rule.get("new_weapon_system_type",0)!=definitions.get(actor,{}).get("new_weapon_system_type",0)
                    or (kind==2 and rule.get("new_equip_type",0)!=n-1)):compatible=False
            if compatible:chosen[index]=slots
        if actor not in actors or active not in chosen or any(r[0]==actor for r in out["loadouts"]):continue
        active_ids={k for k in chosen[active] if k}
        if active_ids & equipped:continue
        equipped.update(active_ids)
        out["loadouts"].append([actor,active,sorted(chosen.items())])
    if "progress_location" in data:
        from save_import_location import validate as validate_location
        location=validate_location(data["progress_location"])
        if location:out["progress_location"]=location
    if "gather_totals" in data:
        scene=out.get("progress_location",{}).get("scene")
        known=_table("Scene","gather_number")
        selected={}
        for entry in rows(data["gather_totals"],6):
            sid,kind,count=row(entry,3)
            number(sid);number(kind,5);number(count,0xffffffff)
            if sid==scene and sid in known and count:
                selected[kind]=max(count,selected.get(kind,0))
        out["gather_totals"]=[[scene,kind,value] for kind,value in sorted(selected.items())]
    totals={"队长等级":out["player"][0],**{LABELS[k]:len(out[k]) for k in LABELS}}
    for kind,key,label in (("persistent","additional_quests","支线、委托及番外等一次性任务"),("history",HISTORY,"未激活的历史任务")):
        values=[record for ident,(category,record) in supplementary.items() if category==kind and ident not in ambiguous]
        if values:
            out[key]=values
            totals[label]=len(values)
    if "field_progress" in data:
        from save_import_field import validate as validate_field
        restored = validate_field(data["field_progress"], out.get("progress_location", {}).get("scene"))
        for key, label in (("devices","区域装置"),("goals","区域目标"),("collectibles","区域收藏点"),
                           ("riddles","世界挑战"),("challenges","限时挑战")):
            if restored[key]:totals[label] = len(restored[key])
        if any(restored[k] for k in restored if k != "scene"):
            out["field_progress"] = restored
    if "pets" in data:
        from save_import_pets import validate as validate_pets
        pets=out["pets"]=validate_pets(data["pets"])
        totals["可恢复宠物"]=len(pets["entries"])
        totals["缺少可用明细的宠物"]=pets["total"]-len(pets["entries"])
    if out.get("gather_totals"):totals["场景采集记录"]=len(out["gather_totals"])
    skipped=sum(len(data[k])-len(out[k]) for k in LABELS)-len(out.get("additional_quests",()))-len(out.get(HISTORY,()))
    totals["穿搭预设"]=sum(len(h[6]) for h in out["heroes"])
    totals["已编队人数"]=sum(bool(i) for i in out["team"])
    totals["装备预设"]=sum(len(presets) for _,_,presets in out["loadouts"])
    totals["跳过记录"]=skipped
    return out,totals


def restore(pid,plan):
    from private_appearance import valid_preset
    level,exp,world=plan["player"]
    for scene,kind,count in plan.get("gather_totals",()):
        db.conn.execute("""INSERT INTO field_gather_counts VALUES(?,0,?,?,?,'',0,0)
            ON CONFLICT(player,scope,place,kind) DO UPDATE SET total=max(total,excluded.total)""",
            (pid,scene,kind,count))
    db.conn.execute("UPDATE players SET level=?,exp=?,world_level=? WHERE player_id=?",(level,exp,world,pid))
    if "head" in plan:
        db.set_players_info(pid,"head",plan["head"])
    for key,count in plan["stacks"]:
        raw=db.get_item_detail(pid,key)
        value=pb.ItemDetail.FromString(raw) if raw else pb.ItemDetail()
        item=value.main_item
        if raw and not item.HasField("base_item"):continue
        item.item_id=item.base_item.item_id=key
        item.item_tag=_table("Item","item")[key]["new_bag_item_tag"]
        item.base_item.num=max(count,item.base_item.num)
        db.set_item_detail(pid,value.SerializeToString(),key)
    for key,schemes in plan["outfits"]:
        raw=db.get_item_detail(pid,key)
        value=pb.ItemDetail.FromString(raw) if raw else pb.ItemDetail()
        item=value.main_item
        item.item_id=item.outfit.outfit_id=key
        item.item_tag=pb.EBagItemTag_Fashion
        saved=item.outfit.dye_schemes
        if not saved:saved.add(is_un_lock=True)
        for index,colors in schemes:
            while len(saved)<=index:saved.add(scheme_index=len(saved))
            target=saved[index];target.is_un_lock=True
            del target.colors[:]
            for p,r,g,b in colors:target.colors.add(pos=p,red=r,green=g,blue=b)
        db.set_item_detail(pid,value.SerializeToString(),key)
    for key,lv,cap,exp,star,skills,looks,active in plan["heroes"]:
        prior=db.get_characters(pid,key)
        c=pb.Character.FromString(prior[0]) if prior else db.add_character(pid,key)
        c.level,c.max_level,c.exp,c.star=lv,cap,exp,star
        skill_map=dict(skills)
        for s in c.character_skill_list:
            if s.skill_id in skill_map:s.skill_level=max(1,min(10,skill_map[s.skill_id]))
        for index,parts,dyes,hide_orn,hide_braid in looks_for_actor(looks):
            preset=next((p for p in c.outfit_presets if p.preset_index==index),None)
            if preset is None:continue
            for slot,wearable,scheme in zip(WEAR,parts,dyes):
                index_field=slot+"_dye_scheme_index"
                if not wearable:
                    setattr(preset,slot,0)
                    setattr(preset,index_field,0)
                    continue
                sample=pb.OutfitPreset()
                setattr(sample,slot,wearable)
                setattr(sample,index_field,scheme)
                if not valid_preset(pid,c.character_id,sample):
                    if not scheme:continue
                    setattr(sample,index_field,0)
                    if not valid_preset(pid,c.character_id,sample):continue
                    scheme=0
                setattr(preset,slot,wearable)
                setattr(preset,index_field,scheme)
            preset.outfit_hide_info.hide_orn=bool(hide_orn)
            preset.outfit_hide_info.hide_braid=bool(hide_braid)
        if active<len(c.outfit_presets):c.in_use_outfit_preset_index=active
        db.set_character(pid,key,c.SerializeToString())
    instances=[]
    for kind,key,first,*second in plan["gear"]:
        value=make_item(key,1,pid,equipment_level=first if kind!=3 else None)
        item=getattr(value.main_item,("weapon","armor","poster")[kind-1])
        if kind==1:item.level,item.star=first,second[0]
        elif kind==2:item.level,item.strength_level=first,second[0]
        else:item.star=first
        if not item.instance_id:raise ValueError("catalog equipment instance unavailable")
        db.set_item_detail(pid,value.SerializeToString(),None,item.instance_id)
        instances.append(item.instance_id)
    if plan.get("team"):
        db.set_players_info(pid,"team",tuple(plan["team"]))
        db.set_players_info(pid,"character_id",plan["team"][0])
    from private_equipment_presets import normalize_character,slots as equipment_slots,preset_at
    for actor,active,presets in plan.get("loadouts",()):
        c=pb.Character.FromString(db.get_characters(pid,actor)[0]);normalize_character(c)
        previous=preset_at(c,c.in_use_equipment_preset_index)
        for kind,_,iid in equipment_slots(previous):
            raw=db.get_item_detail(pid,instance_id=iid)
            if raw:
                item=pb.ItemDetail.FromString(raw)
                getattr(item.main_item,kind).wearer_id=0
                db.set_item_detail(pid,item.SerializeToString(),instance_id=iid)
        for index,refs in presets:
            preset=preset_at(c,index)
            native=[instances[i-1] if i else 0 for i in refs]
            preset.weapon=native[0]
            for i,p in enumerate(preset.armors):p.armor_id=native[1+i]
            for i,p in enumerate(preset.posters):p.poster_id=native[5+i]
        c.in_use_equipment_preset_index=active
        for kind,_,iid in equipment_slots(preset_at(c,active)):
            item=pb.ItemDetail.FromString(db.get_item_detail(pid,instance_id=iid))
            getattr(item.main_item,kind).wearer_id=actor
            db.set_item_detail(pid,item.SerializeToString(),instance_id=iid)
        db.set_character(pid,actor,c.SerializeToString())
    for key,state,count,conditions in plan["quests"]:
        q=make_Quest(pid,key);q.status=state;q.complete_count=count
        by_id={i:(st,n) for i,st,n in conditions}
        for c in q.conditions:c.status,c.progress=by_id[c.condition_id]
        db.set_quest(pid,key,q.SerializeToString())
    from save_import_quests import add_missing
    add_missing(db.conn,pid,plan)
    for key,stories in plan["chapters"]:
        chapter=pb.Chapter(chapter_id=key,rewarded_story_ids=stories)
        db.set_chapter(pid,key,chapter.SerializeToString())
    for key,count in plan["achievements"]:
        raw=db.get_achieve(pid,key)
        a=pb.Achieve.FromString(raw) if raw else pb.Achieve(achieve_id=key)
        a.count=max(a.count,count)
        db.set_achieve(pid,key,a.SerializeToString())
    from save_import_location import restore as restore_location
    restore_location(pid,plan)
    if "field_progress" in plan:
        from save_import_field import restore as restore_field
        restore_field(pid, plan["field_progress"])
    if "pets" in plan:
        from save_import_pets import restore as restore_pets
        restore_pets(pid,plan["pets"])
