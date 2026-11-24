"""Native restaurant arcade grids, orders and once-per-day rewards for each private character."""
from datetime import timedelta
import json
import random

from network.msg_id import MsgId
from private_dungeons import _table
from private_shop import now
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res


def calendar_day():
    return (now()-timedelta(hours=5)).date().isoformat()


def weight(key):
    return int(_table("GlobalConfig","config","key")[key]["values"][0])


def activity_config(activity):
    if "RestaurantGame" not in res:
        return None
    def group(sheet,index):
        row=_table("RestaurantGame",sheet).get(activity,{})
        return {g[index]:g for g in row.get(sheet+"_group_info",[])}
    quests=group("restaurant_game_quest","game_quest_i_d")
    bags=group("restaurant_desk_bag","bag_i_d")
    foods=group("restaurant_game_food","food_i_d")
    pools={k:r for k,r in _table("RestaurantGame","restaurant_random_pool").items() if r.get("activity_i_d")==activity}
    # All four retail themes reference the same stage IDs in their own quest sheets.
    stages=_table("RestaurantGame","restaurant_dungeon")
    requested={q.get("param") for q in quests.values() if not q.get("new_restaurant_game_quest_type")}
    chain={did:stages[did] for did in requested if did in stages}
    roots=[did for did,row in chain.items() if not row.get("unlock_dungeon_i_d") and not row.get("is_refresh")]
    coins={q["game_coin_i_d"] for q in quests.values() if q.get("game_coin_i_d")}
    if not foods or not bags or not pools or len(roots)!=1 or len(coins)!=1 or requested!=chain.keys():
        return None
    return dict(quests=quests,bags=bags,foods=foods,pools=pools,chain=chain,first=roots[0],coin=coins.pop())


def orders(cfg,stage):
    customers=[]
    for order in cfg["chain"][stage]["restaurant_dungeon_group_info"]:
        choices=[i for i,f in cfg["foods"].items() if f.get("food_type")==order.get("food_type")
                 and f.get("food_level")==order.get("food_level")]
        if not choices:raise ValueError("Restaurant stage refers to a missing food type/level")
        customers.append([random.choice(choices),order["order_quantity"]])
    return customers


def write(player,activity,state):
    db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,'restaurant_v1',?,?)",
                    (player,str(activity),json.dumps(state,separators=(",",":"))))


def load(player,activity,cfg,day):
    row=db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns='restaurant_v1' AND k=?",
                        (player,str(activity))).fetchone()
    if row:
        state=json.loads(row[0])
        if state["day"]==day:return state,False
        state["day"]=day
        state["daily_rewards"]=[]
        if cfg["chain"][state["stage"]].get("is_refresh"):
            state["customers"]=orders(cfg,state["stage"])
    else:
        state={"day":day,"stage":cfg["first"],"customers":orders(cfg,cfg["first"]),
               "board":{},"completed":[],"rewards":[],"daily_rewards":[],"last_clear":"",
               "weight":weight("RestaurantGameBaseWeight")}
    write(player,activity,state)
    return state,bool(row)


def unlocked(cfg,state,index):
    row=cfg["bags"].get(index)
    return row is not None and (not row.get("unlock_dungon_i_d") or row["unlock_dungon_i_d"] in state["completed"])


def grid(cfg,state,index,target):
    target.index=index
    target.food=state["board"].get(str(index),0)
    target.is_un_lock=unlocked(cfg,state,index)


def progress(q,state,day):
    if q["game_quest_i_d"] in state["daily_rewards" if q.get("is_refresh") else "rewards"]:
        return pb.QuestStatus_Finish
    done=q["param"] in state["completed"]
    if q.get("new_restaurant_game_quest_type")==1:
        done=True  # retail localised task "每日登录"
    elif q.get("is_refresh"):
        done=state["last_clear"]==day
    return pb.QuestStatus_Completed if done else pb.QuestStatus_InProgress


def dungeon(cfg,state,target):
    target.dungeon_id=state["stage"]
    for index,(food,remaining) in enumerate(state["customers"],1):
        target.customer_list.add(customer_index=index,food=food,status=remaining==0)
    for qid,q in cfg["quests"].items():
        target.question_info.add(quest_id=qid,status=progress(q,state,state["day"]))


def info(session,req,rsp,cfg,state):
    for index in cfg["bags"]:
        grid(cfg,state,index,rsp.grid_list.add())
    dungeon(cfg,state,rsp.dungeon_info)
    return pb.StatusCode_OK


def balances(session,changes,rsp):
    """Validate all new balances before changing any inventory rows."""
    totals=[]
    for item,qty in changes.items():
        if not qty:continue
        raw=db.get_item_detail(session.player_id,item)
        total=pb.ItemDetail.FromString(raw) if raw else make_item(item,0,session.player_id)
        if not total or total.main_item.WhichOneof("item")!="base_item":
            return pb.StatusCode_CFG_NOT_FOUND
        value=total.main_item.base_item.num+qty
        if value<0:return pb.StatusCode_ITEM_NOT_ENOUGH
        if value>0x7fffffffffffffff:return pb.StatusCode_BAD_REQ
        total.main_item.base_item.num=value
        totals.append((item,total,qty))
    notice=pb.PackNotice(status=pb.StatusCode_OK)
    for item,total,qty in totals:
        db.set_item_detail(session.player_id,total.SerializeToString(),item)
        if qty>0:rsp.item_details.add().CopyFrom(make_item(item,qty,session.player_id))
        notice.items.add().CopyFrom(total)
    if totals:session.send(MsgId.PackNotice,notice,0)
    return pb.StatusCode_OK


def operate(session,req,rsp,cfg,state):
    board=state["board"]
    changed={}
    if req.type==pb.RestaurantGameOperateType_Buy:
        pool=cfg["pools"].get(req.buy_pool_id) if req.buy_pool_id else next(iter(cfg["pools"].values()))
        if not pool:return pb.StatusCode_CFG_NOT_FOUND
        target=next((i for i in sorted(cfg["bags"]) if unlocked(cfg,state,i) and str(i) not in board),None)
        if target is None:return pb.StatusCode_MINIGAME_RESTAURANT_GRID_FULL
        choices=[f["food_id"] for f in pool["restaurant_random_pool_group_info"] if f["food_id"] in cfg["foods"]]
        if not choices:return pb.StatusCode_CFG_NOT_FOUND
        cost=random.randint(pool["rate_min"],pool["rate_max"])
        status=balances(session,{cfg["coin"]:-cost},rsp)
        if status!=pb.StatusCode_OK:return status
        changed[target]=random.choice(choices)
    elif req.type in (pb.RestaurantGameOperateType_Mix,pb.RestaurantGameOperateType_Recycle):
        indices=list(req.grid)
        if not indices or len(indices)!=len(set(indices)):return pb.StatusCode_BAD_REQ
        if any(not unlocked(cfg,state,i) for i in indices):return pb.StatusCode_MINIGAME_RESTAURANT_GRID_NOT_EXIST
        if any(str(i) not in board for i in indices):return pb.StatusCode_MINIGAME_RESTAURANT_GRID_EMPTY
        dishes=[board[str(i)] for i in indices]
        changed=dict.fromkeys(indices,0)
        if req.type==pb.RestaurantGameOperateType_Mix:
            if len(indices)<2 or len(set(dishes))!=1:return pb.StatusCode_MINIGAME_RESTAURANT_MIX_ERR
            dish=cfg["foods"][dishes[0]]
            result=dish.get("synthetic_target")
            if not result:return pb.StatusCode_MINIGAME_RESTAURANT_FOOD_MAX_LEVEL
            special=dish.get("synthesis_special_food_i_d")
            if special:
                if random.randint(1,100)<=state["weight"]:
                    result=special
                    state["weight"]=weight("RestaurantGameBaseWeight")
                else:
                    state["weight"]=min(weight("RestaurantGameWeightLimit"),state["weight"]+weight("RestaurantGameWeightNumber"))
            changed[indices[-1]]=result
        else:
            refund=sum(random.randint(cfg["foods"][i]["recycling_price_min"],cfg["foods"][i]["recycling_price_max"]) for i in dishes)
            status=balances(session,{cfg["coin"]:refund},rsp)
            if status!=pb.StatusCode_OK:return status
    else:return pb.StatusCode_BAD_REQ
    for idx,food in changed.items():
        if food:board[str(idx)]=food
        else:board.pop(str(idx),None)
        grid(cfg,state,idx,rsp.grid.add())
    return pb.StatusCode_OK


def eat(session,req,rsp,cfg,state):
    index=req.customer_index-1
    if index<0 or index>=len(state["customers"]):return pb.StatusCode_BAD_REQ
    dish=state["customers"][index]
    if not dish[1]:return pb.StatusCode_MINIGAME_RESTAURANT_CUSTOMER_FINISH
    if not unlocked(cfg,state,req.grid) or state["board"].get(str(req.grid))!=dish[0]:
        return pb.StatusCode_MINIGAME_RESTAURANT_FOOD_ERR
    del state["board"][str(req.grid)]
    dish[1]-=1
    changed={req.grid}
    if not any(remaining for _,remaining in state["customers"]):
        stage=state["stage"]
        if stage not in state["completed"]:
            state["completed"].append(stage)
            changed.update(i for i,row in cfg["bags"].items() if row.get("unlock_dungon_i_d")==stage)
        if cfg["chain"][stage].get("is_refresh"):
            state["last_clear"]=state["day"]
        else:
            following=next((i for i,r in cfg["chain"].items() if r.get("unlock_dungeon_i_d")==stage),None)
            if following:
                state["stage"]=following
                state["customers"]=orders(cfg,following)
    for idx in sorted(changed):grid(cfg,state,idx,rsp.grid.add())
    rsp.customer_index=req.customer_index
    dungeon(cfg,state,rsp.dungeon_info)
    return pb.StatusCode_OK


def reward(session,req,rsp,cfg,state):
    q=cfg["quests"].get(req.quest_id)
    if not q:return pb.StatusCode_CFG_NOT_FOUND
    rsp.quest.quest_id=req.quest_id
    rsp.quest.status=progress(q,state,state["day"])
    if rsp.quest.status==pb.QuestStatus_Finish:return pb.StatusCode_REPEAT_GET_REWARD
    if rsp.quest.status!=pb.QuestStatus_Completed:return pb.StatusCode_FAIL
    status=balances(session,{q["game_coin_i_d"]:q.get("game_coin",0),
                             q["game_integral_item"]:q.get("game_integral",0)},rsp)
    if status!=pb.StatusCode_OK:return status
    state["daily_rewards" if q.get("is_refresh") else "rewards"].append(req.quest_id)
    rsp.quest.status=pb.QuestStatus_Finish
    return pb.StatusCode_OK


def handle(session,data,packet_id,name,action):
    req=getattr(pb,name+"Req").FromString(data)
    rsp=getattr(pb,name+"Rsp")(activity_id=req.activity_id)
    from private_events import active_arcade
    selected = active_arcade()
    if selected is not None and selected != req.activity_id:
        rsp.status = pb.StatusCode_Activity_Close
        session.send(getattr(MsgId,name+"Rsp"),rsp,packet_id)
        return
    cfg=activity_config(req.activity_id)
    if not cfg:
        rsp.status=pb.StatusCode_CFG_NOT_FOUND
    else:
        state,rolled=load(session.player_id,req.activity_id,cfg,calendar_day())
        if rolled:
            notice=pb.RestaurantGameDayNotice(status=pb.StatusCode_OK,activity_id=req.activity_id)
            dungeon(cfg,state,notice.dungeon_info)
            session.send(MsgId.RestaurantGameDayNotice,notice,0)
        rsp.status=action(session,req,rsp,cfg,state)
        if rsp.status==pb.StatusCode_OK and action!=info:write(session.player_id,req.activity_id,state)
    session.send(getattr(MsgId,name+"Rsp"),rsp,packet_id)
