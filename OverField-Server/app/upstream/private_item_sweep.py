"""Quick acquisition of materials from the native character upgrade panel."""
from network.msg_id import MsgId
from private_dungeons import _table, credit_player_experience
from private_gameplay_transaction import run
from private_shop import balance
from proto import net_pb2 as pb
from utils import db


def item_sweep(session, request, response):
    return run(session, request, response, purchase)


def purchase(session, request, response, pending):
    if not request.item_id or not request.count:
        return pb.StatusCode_BAD_REQ
    row = _table("QuickObtaining", "quick_obtaining").get(request.item_id)
    if row is None:
        return pb.StatusCode_CFG_NOT_FOUND
    player = session.player_id
    level, = db.get_players_info(player, "level")
    tier = max((tier for tier in row["quick_obtaining_item"] if tier["player_level"] <= level),
               key=lambda tier: tier["player_level"], default=None)
    if tier is None or tier["num"] <= 0:
        return pb.StatusCode_CFG_NOT_FOUND
    rounds, remainder = divmod(request.count, tier["num"])
    if remainder:
        return pb.StatusCode_BAD_REQ

    # The native QuickAccessButton and DiamondButton confirm the same quantity, with
    # is_sweep true for vitality and false for stars. Neither button sends a preview.
    if request.is_sweep:
        cost = tier["need_vitality"] * rounds
        pending.balances.change(10, -cost)
    else:
        cost = tier["need_specialcurrency"] * rounds
        stars = balance(player, 102)
        free = min(stars.main_item.base_item.num if stars else 0, cost)
        if free:
            pending.balances.change(102, -free)
        if cost > free:
            pending.balances.change(108, free - cost)
    def grant(template, amount):
        total = pending.balances.change(template, amount)
        delta = response.rewards.add().main_item
        delta.CopyFrom(total)
        delta.base_item.num = amount

    grant(request.item_id, request.count)
    if request.is_sweep:
        # Community settlement rule: ten points of captain experience per vitality.
        experience = cost * 10
        grant(1, experience)
        pending.notices.append((MsgId.PlayerLevelExpNotice, credit_player_experience(player, experience)))
    return pb.StatusCode_OK
