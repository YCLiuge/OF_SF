"""National PC furniture, handicraft and sewing recipes, proficiency and personal collection."""
from collections import Counter
from datetime import timedelta
import random

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res
from private_shop import balance, fixed_gift, in_window, now

GROUP = {pb.LIFE_TYPE_Fishing: "Fishing", pb.LIFE_TYPE_Making: "Make", pb.LIFE_TYPE_Handicraft: "Handicraft", pb.LIFE_TYPE_Sewing: "Spin"}
MAX_COUNT = 0xffffffff


def rows(kind, table):
    name = GROUP[kind]
    return res.index(name, name.lower() + "_" + table)


def base(player, kind):
    records = db.get_life(player) or {}
    return pb.LifeBaseInfo.FromString(records[kind]) if kind in records else None


def read_counts(player, kind):
    prefix = f"{kind}:"
    records = db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns='life_book' AND k LIKE ?",
                              (player, prefix + "%"))
    for key, value in records:
        item = key[len(prefix):]
        if item.isdecimal() and 0 < int(item) <= MAX_COUNT and int(value) > 0:
            yield int(item), int(value)


def book(session, req, rsp):
    if req.life_type not in range(1, 7):
        return pb.StatusCode_BAD_REQ
    for item, count in read_counts(session.player_id, req.life_type):
        rsp.life_achieve_count_lst.add(id=item, count=count)
    return pb.StatusCode_OK


def rewards(kind):
    if kind == pb.LIFE_TYPE_Gathering:
        return {a["achieve_condition_i_d"]:a["reward_i_d"] for r in res.index("LifeGather","life_gather_item").values()
                for a in r.get("life_gather_achieve_rewards",())}
    if kind == pb.LIFE_TYPE_Fishing:
        return {goal["achieve_condition_i_d"]:goal["reward_i_d"] for fish in rows(kind,"info").values()
                for goal in fish.get("fish_achieve_rewards",())}
    tag = GROUP[kind].lower() + "_achieve_rewards"
    return {a["achieve_condition_i_d"]: a["reward_i_d"]
            for recipe in rows(kind, "item").values() for a in recipe.get(tag, ())}


def fill_achievements(player, kind, output):
    output.life_type = kind
    if kind not in GROUP and kind != pb.LIFE_TYPE_Gathering:
        return
    allowed = set(rewards(kind))
    if kind in GROUP:
        tasks = "fishing_level_quest" if kind == pb.LIFE_TYPE_Fishing else GROUP[kind].lower() + "_level_achieve"
        for row in rows(kind, "level").values():
            allowed.update(a["achieve_condition_i_d"] for a in row.get(tasks, ()))
    for aid, raw in db.conn.execute("SELECT achieve_id,achieve_blob FROM achieve WHERE player_id=?", (player,)):
        if aid in allowed:
            entry = pb.Achieve.FromString(raw)
            if entry.count:
                output.life_achieve_.add().CopyFrom(entry)
    for (key,) in db.conn.execute("SELECT key FROM claimed_rewards WHERE player_id=? AND system='life_achieve'", (player,)):
        if str(key).isdecimal() and int(key) in allowed:
            output.rewarded_life_achieve_id_lst.append(int(key))


def info(session, req, rsp):
    detail = base(session.player_id, req.life_type)
    if not detail:
        return pb.StatusCode_BAD_REQ
    rsp.life_base_info.CopyFrom(detail)
    from private_life_skills import refresh, write_entries
    refresh(session)
    rsp.life_skill.life_type = req.life_type
    write_entries(session.player_id, req.life_type, rsp.life_skill.life_skill_info)
    fill_achievements(session.player_id, req.life_type, rsp.life_achieve)
    return pb.StatusCode_OK


def achievements(session, req, rsp):
    if req.life_type not in GROUP and req.life_type != pb.LIFE_TYPE_Gathering:
        return pb.StatusCode_MODULE_CLOSE
    fill_achievements(session.player_id, req.life_type, rsp.life_achieve)
    return pb.StatusCode_OK


def stack(player, template):
    item = balance(player, template)
    if item is None and template in res.index("Item", "item"):
        item = make_item(template, 0, player)
    return item if isinstance(item, pb.ItemDetail) and item.main_item.WhichOneof("item") == "base_item" else None


def payments(player, costs):
    details = {}
    for template, count in costs.items():
        item = stack(player, template)
        if not item or item.main_item.base_item.num < count:
            return None, pb.StatusCode_EnergyNotEnough if template == 11 else pb.StatusCode_ITEM_NOT_ENOUGH
        item.main_item.base_item.num -= count
        details[template] = item
    return details, pb.StatusCode_OK


def save_pack(session, details):
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    for template, item in details.items():
        db.set_item_detail(session.player_id, item.SerializeToString(), template)
        notice.items.add().CopyFrom(item)
    session.send(MsgId.PackNotice, notice, 0)


def previous_count(player, aid):
    raw = db.get_achieve(player, aid)
    return pb.Achieve.FromString(raw).count if raw else 0


def daily_proficiency(player,aid,day,old,new,condition,task):
    key = f"{day}:{aid}"
    db.conn.execute("INSERT OR IGNORE INTO player_kv VALUES(?,'life_proficiency_base',?,?)", (player,key,old))
    start = int(db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='life_proficiency_base' AND k=?",
                                (player,key)).fetchone()[0])
    threshold,limit = max(condition.get("count_param",1),1),task.get("complete_limit",MAX_COUNT)
    before = min(limit,max(0,old-start)//threshold)
    after = min(limit,max(0,new-start)//threshold)
    return (after-before)*task.get("proficiency",0)


def note_success(session, kind, recipe, life):
    from private_bless_tasks import record as prayer
    prayer(session,{pb.LIFE_TYPE_Handicraft:49,pb.LIFE_TYPE_Making:50,pb.LIFE_TYPE_Sewing:51}[kind])
    group = GROUP[kind].lower()
    output = recipe["get_item_i_d"]
    player = session.player_id
    db.conn.execute("""INSERT INTO player_kv VALUES(?,'life_book',?,1)
                       ON CONFLICT(player_id,ns,k) DO UPDATE SET v=CAST(v AS INTEGER)+1""",
                    (player, f"{kind}:{output}"))
    conditions = res.index("Achieve", "achieve")
    active = rows(kind, "level")[life.level].get(group + "_level_achieve", ())
    daily = {a["achieve_condition_i_d"]: a for a in active}
    aids = set(daily)
    aids.update(a["achieve_condition_i_d"] for a in recipe.get(group + "_achieve_rewards", ()))
    day = (now() - timedelta(hours=4)).date().isoformat()
    gain = 0
    notice = pb.LifeAchieveNotice(status=pb.StatusCode_OK)
    changed = notice.life_achieves.add(life_type=kind)
    for aid in sorted(aids):
        condition = conditions.get(aid, {})
        event = condition.get("new_achieve_condition")
        if event in (55, 56, 57):
            matches = output in condition.get("param", ())
        elif event == 48:
            matches = kind == pb.LIFE_TYPE_Sewing
        elif event == 51:
            matches = kind == pb.LIFE_TYPE_Sewing and res.index("Item","item")[output]["new_bag_item_tag"] == pb.EBagItemTag_Material
        elif event == 47:
            matches = kind == pb.LIFE_TYPE_Making
        elif event == 46:
            matches = kind == pb.LIFE_TYPE_Handicraft
        elif event == 49:
            matches = kind == pb.LIFE_TYPE_Handicraft and res.index("Item", "item")[output]["new_bag_item_tag"] == 8
        else:
            matches = False
        if not matches:
            continue
        old = previous_count(player, aid)
        updated = changed.life_achieve_.add(achieve_id=aid, count=min(old + 1, MAX_COUNT))
        db.set_achieve(player, aid, updated.SerializeToString())
        if aid in daily:
            gain += daily_proficiency(player,aid,day,old,updated.count,condition,daily[aid])
    if changed.life_achieve_:
        session.send(MsgId.LifeAchieveNotice, notice, 0)
    if gain:
        life.proficiency_value = min(MAX_COUNT, life.proficiency_value + gain)
        db.set_life(player, kind, life.SerializeToString())
        notice = pb.LifeProficiencyNotice(status=pb.StatusCode_OK)
        notice.life_proficiency.life_type = kind
        notice.life_proficiency.proficiency_value = life.proficiency_value
        session.send(MsgId.LifeProficiencyNotice, notice, 0)


def craft(session, kind, recipe_id, rsp):
    player = session.player_id
    recipe = rows(kind, "item").get(recipe_id)
    if not recipe:
        return pb.StatusCode_CFG_NOT_FOUND
    if not in_window(recipe, now()):
        return pb.StatusCode_MODULE_CLOSE
    group = GROUP[kind].lower()
    life = base(player, kind)
    if not life or life.level < recipe[group + "_level"]:
        return pb.StatusCode_LifeLvNotEnough
    formula = recipe.get("need_formula_item_i_d")
    if formula:
        proof = stack(player, formula)
        if not proof or proof.main_item.base_item.num < 1:
            return pb.StatusCode_ITEM_NOT_ENOUGH
    costs = Counter({recipe["need_item_i_d"]: recipe["need_item_count"]})
    for cost in recipe[group + "_items"]:
        costs[cost["item_i_d"]] += cost["item_count"]
    changes, status = payments(player, costs)
    if changes is None:
        return status
    output = recipe["get_item_i_d"]
    product = changes.get(output) or balance(player, output) or make_item(output, 0, player)
    item_type = product.main_item.WhichOneof("item") if product else None
    if item_type not in ("base_item", "outfit") or (item_type=="base_item" and product.main_item.base_item.num >= MAX_COUNT):
        return pb.StatusCode_BAD_REQ
    result = rsp.life_common_result
    result.life_type = kind
    result.is_success = random.random() < recipe["successful_probability"]
    if result.is_success:
        if item_type=="base_item":
            product.main_item.base_item.num += 1
        changes[output] = product
        delta = result.result_items.add()
        delta.CopyFrom(product)
        if item_type=="base_item":
            delta.main_item.base_item.num = 1
        note_success(session, kind, recipe, life)
    save_pack(session, changes)
    return pb.StatusCode_OK


def claim(session, req, rsp):
    kind, aid, player = req.life_type, req.achieve_id, session.player_id
    table = rewards(kind) if kind in GROUP or kind == pb.LIFE_TYPE_Gathering else {}
    if aid not in table:
        return pb.StatusCode_CFG_NOT_FOUND
    threshold = res.index("Achieve", "achieve")[aid].get("count_param", 1)
    if previous_count(player, aid) < threshold:
        return pb.StatusCode_FAIL
    if db.conn.execute("SELECT 1 FROM claimed_rewards WHERE player_id=? AND system='life_achieve' AND key=?",
                       (player, str(aid))).fetchone():
        return pb.StatusCode_REPEAT_GET_REWARD
    amounts = fixed_gift(player, table[aid], db.get_players_info(player, "level")[0])
    if not amounts:
        return pb.StatusCode_CFG_NOT_FOUND
    details = {}
    for item, count in amounts.items():
        stock = stack(player, item)
        if not stock or stock.main_item.base_item.num + count > MAX_COUNT:
            return pb.StatusCode_CFG_NOT_FOUND
        stock.main_item.base_item.num += count
        details[item] = stock
    for item, stock in details.items():
        awarded = rsp.items.add()
        awarded.CopyFrom(stock)
        awarded.main_item.base_item.num = amounts[item]
    db.conn.execute("INSERT INTO claimed_rewards(player_id,system,key) VALUES(?,'life_achieve',?)", (player, str(aid)))
    rsp.rewarded_achieve_ids.append(aid)
    save_pack(session, details)
    return pb.StatusCode_OK


def level_up(session, req, rsp):
    kind, player = req.life_type, session.player_id
    if kind not in GROUP:
        return pb.StatusCode_MODULE_CLOSE
    life = base(player, kind)
    levels = rows(kind, "level")
    if not life or life.level not in levels:
        return pb.StatusCode_CFG_NOT_FOUND
    rsp.life_base_info.CopyFrom(life)
    if life.level + 1 not in levels:
        return pb.StatusCode_LifeMaxLv
    row = levels[life.level]
    cost = row["upgrade_level_by_proficiency"]
    if life.proficiency_value < cost:
        return pb.StatusCode_ProficiencyNotEnough
    cost_fields = ("up_level_need_item_i_d","up_level_need_item_count") if kind==pb.LIFE_TYPE_Fishing else ("need_item_i_d","need_item_count")
    changes, status = payments(player, {row[cost_fields[0]]: row[cost_fields[1]]})
    if changes is None:
        return status
    life.level += 1
    life.proficiency_value -= cost
    db.set_life(player, kind, life.SerializeToString())
    rsp.life_base_info.CopyFrom(life)
    save_pack(session, changes)
    return pb.StatusCode_OK
