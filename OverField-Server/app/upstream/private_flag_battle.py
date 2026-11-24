"""Native overworld battle and scenic flags, with role-specific map state."""
from collections import OrderedDict
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import _table
from utils import db
from utils.pb_create import make_QuestNotice

def public_battles(scene):
    return {stage["relation_i_d"] for row in _table("Flag","flag").values()
            if row.get("scene_i_d")==scene and row.get("icon_i_d")==1
            for stage in row["flag_group_info"]
            if stage.get("new_mission_type",0)==pb.EMissionType_NormalBattle
            and stage["index"]==1 and stage.get("is_item_unlock")}


def scenic_spots(scene):
    return {stage["relation_i_d"] for row in _table("Flag","flag").values()
            if row.get("scene_i_d")==scene and row.get("icon_i_d")==3
            for stage in row["flag_group_info"] if stage.get("new_mission_type")==pb.EMissionType_ScenicSpot}


def finish_objectives(session, key):
    goals = (aid for aid,row in _table("Achieve","achieve").items()
             if row.get("new_achieve_condition")==4 and row.get("param")==[key])
    notice = make_QuestNotice(session,goals)
    if notice:
        session.send(MsgId.QuestNotice,notice,0)


def open_scenic(session,req,rsp):
    pid,scene,spot = session.player_id,session.scene_id,req.battle_id
    if session.dungeon[0] or spot not in scenic_spots(scene) or req.battle_state!=pb.Start:
        return pb.StatusCode_BAD_REQ
    cfg = _table("Scenic","scenic").get(spot)
    if not cfg or not cfg.get("first_reward_i_d"):
        return pb.StatusCode_CFG_NOT_FOUND
    first = db.conn.execute("INSERT OR IGNORE INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,1)",
                            (pid,f"scenic_spots:{scene}",str(spot))).rowcount
    if first:
        from private_dungeons import grant, reward_items
        grant(session,reward_items(pid,cfg["first_reward_i_d"]),pb.ClaimQuestRewardRsp().items)
    finish_objectives(session,spot)
    rsp.battle_data.battle_id = spot
    rsp.battle_data.type = req.mission_type
    rsp.battle_data.state = pb.Finish
    rsp.battle_data.finish_times = 1
    rsp.battle_data.voice_id = cfg.get("guide_voice",0)
    return pb.StatusCode_OK


def prior_completion(session, quest):
    # A scenic point only sends its open request once. Existing field progress also
    # counts when joining the associated quest after its first victory.
    lookup = {100053:("flag_wins:1","110210",11000531), 100064:("flag_wins:1","110300",11000641),
              100074:("scenic_spots:1","1002",11000741)}
    required = lookup.get(quest.quest_id)
    if required:
        ns, key, condition = required
        row = db.conn.execute("SELECT v FROM player_kv WHERE player_id=? AND ns=? AND k=?",
                              (session.player_id,ns,key)).fetchone()
        if row and int(row[0])>0:
            return condition


def rows(pid, scene):
    return db.conn.execute("SELECT k,v FROM player_kv WHERE player_id=? AND ns=?",
                           (pid,f"flag_wins:{scene}"))


def fill(session, data):
    for key, in db.conn.execute("SELECT k FROM player_kv WHERE player_id=? AND ns=?",
                                (session.player_id,f"scenic_spots:{session.scene_id}")):
        cfg = _table("Scenic","scenic")[int(key)]
        data.flags.add(battle_id=int(key), type=pb.EMissionType_ScenicSpot, state=pb.Finish, finish_times=1,
                       voice_id=cfg.get("guide_voice",0))
    for key, amount in rows(session.player_id,session.scene_id):
        data.flags.add(battle_id=int(key), type=pb.EMissionType_NormalBattle,
                       state=pb.Finish, finish_times=int(amount))


def update(session, req, rsp, packet_id):
    scene, pid = session.scene_id, session.player_id
    if req.mission_type==pb.EMissionType_ScenicSpot:
        return open_scenic(session,req,rsp)
    if req.mission_type!=pb.EMissionType_NormalBattle:
        return pb.StatusCode_MODULE_CLOSE
    if session.dungeon[0] or req.battle_id not in public_battles(scene):
        return pb.StatusCode_BAD_REQ
    key = (scene,req.battle_id)
    active = getattr(session,"active_flag",None)
    receipt = (packet_id,key,req.battle_state)
    receipts = getattr(session,"flag_receipts",None)
    if receipts is None:
        receipts = session.flag_receipts = OrderedDict()
    if packet_id and receipt in receipts:
        rsp.ParseFromString(receipts[receipt])
        return pb.StatusCode_OK
    amount = int(dict(rows(pid,scene)).get(str(req.battle_id),0))
    if req.battle_state == pb.Start:
        if active not in (None,key):
            return pb.StatusCode_BAD_REQ
        session.active_flag = key
    elif req.battle_state in (pb.Finish,pb.Fail,pb.Close,pb.Reset):
        if active!=key:
            return pb.StatusCode_BAD_REQ
        session.active_flag = None
        if req.battle_state==pb.Finish:
            amount+=1
            db.conn.execute("INSERT OR REPLACE INTO player_kv(player_id,ns,k,v) VALUES(?,?,?,?)",
                            (pid,f"flag_wins:{scene}",str(req.battle_id),str(amount)))
            # The national quest conditions for a cleared flag use its battle ID.
            finish_objectives(session,req.battle_id)
    else:
        return pb.StatusCode_BAD_REQ
    rsp.battle_data.battle_id = req.battle_id
    rsp.battle_data.type = req.mission_type
    rsp.battle_data.state = req.battle_state
    rsp.battle_data.finish_times = amount
    if packet_id:
        receipts[receipt] = rsp.SerializeToString()
        if len(receipts)>128:
            receipts.popitem(last=False)
    return pb.StatusCode_OK
