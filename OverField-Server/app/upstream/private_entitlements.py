"""Once-per-player private-realm balances and native inventory/collection unlocks."""
from dataclasses import dataclass
from types import MappingProxyType

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res

INITIAL_BALANCE = 1_000_000
VERSION = "national-1.1.12-v1"
HEAD_VERSION = "national-1.1.12-heads-v1"
# Item bag tag 9 also includes stamina, progression counters, store products and
# physical-merchandise redemption markers. Only spendable currencies belong here.
CURRENCY_IDS = (
    101, 102, 103, 104, 105, 107, 108, 109, 110, 111, 112, 113, 114, 117, 119,
    120, 121, 122, 124, 125, 126, 131, 132, 133, 134,
    1600, 1601, 1602, 1603, 1604, 1605, 1606, 1607,
    6007, 6013, 6019, 6030, 6031, 6032, 6033, 6034, 6035, 6036, 6039, 6040, 6043, 6053,
)


def _rows(table, section):
    return res[table][section]["datas"]


@dataclass(frozen=True)
class Catalog:
    items: object
    furniture: frozenset
    animations: frozenset
    menu_actions: frozenset
    emoji: frozenset
    requirements: object
    head_stickers: object


def catalog():
    items = {row["i_d"]:row for row in _rows("Item","item")}
    achievements = {row["i_d"]:row for row in _rows("Achieve","achieve") if row.get("i_d")}
    animations = {row["i_d"] for row in _rows("Action","action_info") if row.get("anim_prefab")}
    furniture = {row["item_i_d"] for row in _rows("HomeItem","home_item")
                 if row.get("path") and items.get(row.get("item_i_d"),{}).get("new_bag_item_tag")==pb.EBagItemTag_Furniture}
    fashion = {row["item_i_d"] for row in _rows("Fashion","fashion_info")
               if row.get("asset_full_name") and items.get(row.get("item_i_d"),{}).get("new_bag_item_tag")==pb.EBagItemTag_Fashion}
    amounts = {item:1 for item in furniture | fashion}
    actions = set()
    emoji = set()
    requirements = {}

    def collect_condition(row, tag):
        condition = row.get("achieve_condition_i_d")
        if not condition:
            return
        rule = achievements.get(condition,{})
        if (rule.get("new_achieve_condition")!=2 or rule.get("new_function_of_achievement")!=22
                or len(rule.get("param",()))!=1 or rule.get("count_param")!=1):
            return
        template = rule["param"][0]
        # Some visible chat icons have a condition but no corresponding inventory template.
        # Keep their native chat unlock without fabricating an item.
        requirements[condition] = rule["count_param"]
        if items.get(template,{}).get("new_bag_item_tag")==tag:
            amounts[template] = 1

    for entry in _rows("Expression","expression"):
        for group in entry["expression_group_info"]:
            clip = group.get("action_i_d")
            if clip not in animations:
                continue
            actions.add(entry["i_d"])
            collect_condition(group,pb.EBagItemTag_Item)
            material = group.get("need_item_i_d")
            if material in items and group.get("need_item_count",0)>0:
                amounts[material] = max(amounts.get(material,0),group["need_item_count"])
    for entry in _rows("Chat","chat_emotion"):
        if entry.get("icon_i_d") or entry.get("icon_sketch_i_d"):
            emoji.add(entry["i_d"])
            collect_condition(entry,pb.EBagItemTag_Expression)

    for coin in CURRENCY_IDS:
        if items[coin]["new_bag_item_tag"]!=pb.EBagItemTag_Currency:
            raise ValueError(f"Unsupported currency in resource set: {coin}")
        amounts[coin] = INITIAL_BALANCE
    head_ids = {entry["head_i_d"] for entry in _rows("Head","head")}
    if any(items.get(key,{}).get("new_bag_item_tag")!=pb.EBagItemTag_Head for key in head_ids):
        raise ValueError("The avatar directory contains an unknown sticker")
    templates = {}
    head_stickers = {}
    for key,num in sorted((dict.fromkeys(head_ids,1) | amounts).items()):
        row = items[key]
        detail = pb.ItemDetail(extra_quality=row.get("quality",0))
        info = detail.main_item
        info.item_id = key
        info.item_tag = row["new_bag_item_tag"]
        if info.item_tag==pb.EBagItemTag_Fashion:
            info.outfit.outfit_id = key
            info.outfit.dye_schemes.add().is_un_lock = True
        else:
            info.base_item.item_id = key
            info.base_item.num = num
        if key in head_ids:
            head_stickers[key] = detail.SerializeToString()
        else:
            templates[key] = detail.SerializeToString()
    return Catalog(MappingProxyType(templates),frozenset(furniture),frozenset(animations),
                   frozenset(actions),frozenset(emoji),MappingProxyType(requirements),MappingProxyType(head_stickers))


def grant_head_stickers(conn,player,stickers):
    added=conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?,'private_entitlements',?)",
                       (player,HEAD_VERSION))
    if not added.rowcount:
        return False
    fill_head_stickers(conn,player,stickers)
    return True


def fill_head_stickers(conn,player,stickers):
    # A prior collection can contain extra copies, metadata or a sticker with zero stock.
    slots=",".join("?" for _ in stickers)
    existing=dict(conn.execute(f"SELECT item_id,item_detail_blob FROM items WHERE player_id=? AND item_id IN ({slots})",
                               (player,*stickers)))
    updates=[]
    for key,blob in stickers.items():
        if key in existing:
            detail=pb.ItemDetail.FromString(existing[key])
            item=detail.main_item
            if item.item_id!=key or item.item_tag!=pb.EBagItemTag_Head or not item.HasField("base_item"):
                raise ValueError("Invalid stored avatar sticker")
            if item.base_item.num:
                continue
            item.base_item.num=1
            blob=detail.SerializeToString()
        updates.append((player,key,blob))
    conn.executemany("INSERT INTO items(player_id,item_id,item_detail_blob) VALUES(?,?,?) "
                     "ON CONFLICT(player_id,item_id) DO UPDATE SET item_detail_blob=excluded.item_detail_blob",updates)


def grant(conn, player, definitions):
    """The caller owns the SQLite transaction. The receipt and every grant commit together."""
    new_heads=grant_head_stickers(conn,player,definitions.head_stickers)
    acquired=conn.execute("INSERT OR IGNORE INTO claimed_rewards(player_id,system,key) VALUES(?, 'private_entitlements', ?)",
                          (player,VERSION))
    if not acquired.rowcount:
        return new_heads
    placed = {pb.FurnitureDetailsInfo.FromString(raw).furniture_item_id for raw, in conn.execute(
        "SELECT furniture_detail_blob FROM furnitures WHERE player_id=?",(player,))}
    placed.update(row[0] for row in conn.execute("SELECT item FROM realm_hand_furniture WHERE player=?",(player,)))
    existing = dict(conn.execute("SELECT item_id,item_detail_blob FROM items WHERE player_id=?",(player,)))
    changes = []
    for key,template in definitions.items.items():
        prior = existing.get(key)
        if key in placed:
            continue
        if prior is None:
            changes.append((player,key,template))
            continue
        detail = pb.ItemDetail.FromString(prior)
        info = detail.main_item
        if info.item_id != key:
            raise ValueError(f"Inventory key and native item differ for item {key}")
        if info.HasField("outfit"):
            continue  # Preserve presets and owned dye schemes verbatim.
        if not info.HasField("base_item"):
            raise ValueError(f"Unexpected inventory representation for item {key}")
        target = INITIAL_BALANCE if key in CURRENCY_IDS else max(info.base_item.num,1)
        if info.base_item.num != target:
            info.base_item.num = target
            changes.append((player,key,detail.SerializeToString()))
    conn.executemany("INSERT INTO items(player_id,item_id,item_detail_blob) VALUES(?,?,?) "
                     "ON CONFLICT(player_id,item_id) DO UPDATE SET item_detail_blob=excluded.item_detail_blob",changes)

    prior_counts = dict(conn.execute("SELECT achieve_id,achieve_blob FROM achieve WHERE player_id=?",(player,)))
    changes = []
    for condition,minimum in definitions.requirements.items():
        raw = prior_counts.get(condition)
        entry = pb.Achieve.FromString(raw) if raw else pb.Achieve(achieve_id=condition)
        if entry.count<minimum:
            entry.count=minimum
            changes.append((player,condition,entry.SerializeToString()))
    conn.executemany("INSERT INTO achieve(player_id,achieve_id,achieve_blob) VALUES(?,?,?) "
                     "ON CONFLICT(player_id,achieve_id) DO UPDATE SET achieve_blob=excluded.achieve_blob",changes)
    return True


def migrate(conn, definitions):
    players=conn.execute("""SELECT p.player_id FROM players p WHERE
            NOT EXISTS (SELECT 1 FROM claimed_rewards c WHERE c.player_id=p.player_id AND c.system='private_entitlements' AND c.key=?)
            OR NOT EXISTS (SELECT 1 FROM claimed_rewards c WHERE c.player_id=p.player_id AND c.system='private_entitlements' AND c.key=?)""",
            (VERSION,HEAD_VERSION)).fetchall()
    for player, in players:
        with conn:
            grant(conn,player,definitions)


def notify(session):
    data = session.entitlements
    actions = pb.AchieveActionUnLockNotice(status=pb.StatusCode_OK)
    actions.action_id_lst.extend(sorted(data.menu_actions))
    session.send(MsgId.AchieveActionUnLockNotice,actions,0)
    emoji = pb.ChatUnLockExpressionNotice(status=pb.StatusCode_OK)
    emoji.expression_id.extend(sorted(data.emoji))
    session.send(MsgId.ChatUnLockExpressionNotice,emoji,0)
