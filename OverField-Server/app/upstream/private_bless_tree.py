"""National 1.1.12 blessing-tree layouts, private balances and per-character redemptions."""
from collections import Counter

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils.res_loader import res
import private_shop as shop
from private_shop import now
from utils import db


def layout(tree):
    row = res.index("BlessingTree", "blessing_tree_info").get(tree, {})
    return {node["box_i_d"]: node for node in row.get("blessing_tree_info_group_info", ())}


def known_nodes(stored, nodes):
    # Earlier servers accidentally appended any requested box, including unknown IDs.
    return list(dict.fromkeys(node for node in stored if node in nodes))


def query(session, req, rsp):
    rsp.def_id = req.def_id
    nodes = layout(req.def_id)
    if not nodes:
        return pb.StatusCode_CFG_NOT_FOUND
    rsp.tress.SetInParent()
    rsp.tress.tree_ids.extend(known_nodes(db.get_bless_tree(session.player_id, req.def_id), nodes))
    return pb.StatusCode_OK


def restore_legacy_items(player, earned):
    # The old redemption handler activated the base_item oneof on equipment and outfits
    # and stored them under a template ID. Recover only rows with this specific signature.
    for template, confirmed in earned.items():
        stock = shop.balance(player, template)
        if not stock:
            continue
        info = stock.main_item
        if (info.item_id != template or info.WhichOneof("item") != "base_item"
                or info.base_item.item_id != 0 or not 1 <= info.base_item.num <= confirmed):
            continue
        db.conn.execute("DELETE FROM items WHERE player_id=? AND item_id=?", (player, template))
        shop.award(player, {template:info.base_item.num}, pb.ShopBuyRsp(), pb.PackNotice())


def login_notice(player):
    notice = pb.BlessTreeNotice(status=pb.StatusCode_OK)
    stored = db.get_bless_tree(player)
    catalog = res.index("Item", "item")
    typed = shop.INSTANCE_TAGS | {pb.EBagItemTag_Fashion}
    earned = Counter()
    for tree in res.index("BlessingTree", "blessing_tree_info"):
        nodes = layout(tree)
        claims = known_nodes(stored.get(tree, ()), nodes)
        notice.trees[tree].tree_ids.extend(claims)
        for key in claims:
            row = nodes[key]
            template = row["reward_item_i_d"]
            if catalog[template]["new_bag_item_tag"] in typed:
                earned[template] += row["reward_item_count"]
    restore_legacy_items(player, earned)
    return notice


def is_open(tree, period):
    from private_events import _decoded,raw_settings
    from realm_blessings import dates
    overrides=_decoded(raw_settings()).get("blessings",())
    match=next((row for row in overrides if row["id"]==tree),None)
    if match is None:
        return shop.in_window(period,now())
    start,end=dates(match)
    return match["enabled"] and start<=now().strftime("%Y-%m-%d %H:%M:%S")<=end


def unlock(session, req, rsp):
    rsp.def_id, rsp.tree_id = req.def_id, req.tree_id
    player = session.player_id
    nodes = layout(req.def_id)
    node = nodes.get(req.tree_id)
    if not node:
        return pb.StatusCode_CFG_NOT_FOUND
    got = known_nodes(db.get_bless_tree(player, req.def_id), nodes)
    if req.tree_id in got:
        return pb.StatusCode_PLAYER_BLESS_TREE_EXIST
    period = res.index("BlessingTree", "blessing_tree", "blessing_tree_info_i_d").get(req.def_id)
    if not period or not is_open(req.def_id, period):
        return pb.StatusCode_Activity_Close
    parent = node.get("relate_box_i_d")
    if parent and parent not in got:
        return pb.StatusCode_PLAYER_BLESS_NEED_NODE
    currency, amount = node["item_i_d"], node["item_count"]
    stock = shop.balance(player, currency)
    if not stock or stock.main_item.WhichOneof("item") != "base_item" or stock.main_item.base_item.num < amount:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    # Use the shared typed grant path so fashion keeps its dye presets and equipment has an instance ID.
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    grant = pb.ShopBuyRsp()
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")
    db.conn.execute("SAVEPOINT bless_tree_node")
    try:
        stock.main_item.base_item.num -= amount
        db.set_item_detail(player, stock.SerializeToString(), currency)
        shop.award(player, {node["reward_item_i_d"]:node["reward_item_count"]}, grant, notice)
        db.set_bless_tree(player, req.def_id, got + [req.tree_id])
    except (shop.UnsupportedGoods, OverflowError):
        db.conn.execute("ROLLBACK TO bless_tree_node")
        return pb.StatusCode_CFG_NOT_FOUND
    except BaseException:
        db.conn.execute("ROLLBACK TO bless_tree_node")
        raise
    finally:
        db.conn.execute("RELEASE bless_tree_node")
    if node["reward_item_i_d"] != currency:
        notice.items.add().CopyFrom(stock)
    rsp.rewards.extend(grant.items)
    session.send(MsgId.PackNotice, notice, 0)
    return pb.StatusCode_OK
