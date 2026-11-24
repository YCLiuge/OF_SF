"""Settle the cook-food recipe's actual materials and publish a success only after delivering the dish."""
from collections import Counter
import random

from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_crafts import base, payments, save_pack, stack, MAX_COUNT
from private_dungeons import _table
from private_story_objectives import cooked
from utils import db
from utils.pb_create import make_item


def prepare(session,req,rsp):
    pid=session.player_id
    food=_table("Cooking","cook_food").get(req.food_id)
    if not food:
        return pb.StatusCode_CFG_NOT_FOUND
    life=base(pid,pb.LIFE_TYPE_Cooking)
    if food.get("cook_level",1)>(life.level if life else 1):
        return pb.StatusCode_LifeLvNotEnough
    formula=food.get("need_formula_item_i_d")
    if formula and (not (owned:=stack(pid,formula)) or not owned.main_item.base_item.num):
        return pb.StatusCode_ITEM_NOT_ENOUGH
    costs=Counter({food["need_item_i_d"]:food["need_item_count"]})
    for row in food.get("cook_food_items",()):
        costs[row["food_item_i_d"]] += row["food_item_count"]
    changes,status=payments(pid,costs)
    if changes is None:
        return status
    output=food["get_item_i_d"]
    product=changes.get(output) or stack(pid,output)
    if not product or product.main_item.base_item.num>=MAX_COUNT:
        return pb.StatusCode_BAD_REQ
    result=rsp.life_common_result
    result.life_type=pb.LIFE_TYPE_Cooking
    result.is_success=random.random()<food["successful_probability"]
    if result.is_success:
        product.main_item.base_item.num+=1
        changes[output]=product
        delta=result.result_items.add()
        delta.CopyFrom(product)
        delta.main_item.base_item.num=1
        db.conn.execute("""INSERT INTO player_kv VALUES(?,'life_book',?,1)
                        ON CONFLICT(player_id,ns,k) DO UPDATE SET v=CAST(v AS INTEGER)+1""",
                        (pid,f"{pb.LIFE_TYPE_Cooking}:{output}"))
    else:
        for row in food.get("cook_food_items",()):
            result.result_items.add().CopyFrom(make_item(row["food_item_i_d"],row["food_item_count"],pid))
    save_pack(session,changes)
    return pb.StatusCode_OK


@packet_handler(MsgId.CookingFoodReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.CookingFoodReq.FromString(data)
        rsp=pb.CookingFoodRsp()
        rsp.status=prepare(session,req,rsp)
        session.send(MsgId.CookingFoodRsp,rsp,packet_id)
        if rsp.status==pb.StatusCode_OK and rsp.life_common_result.is_success:
            cooked(session)
