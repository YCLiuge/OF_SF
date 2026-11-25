"""Short-lived plaza interactions scoped to the current scene and channel membership."""
import math
import random
import time

from network.msg_id import MsgId
from private_crafts import payments, save_pack
from utils.res_loader import res
from private_request import set_after_commit
from private_social_profile import config, friend_points
from proto import net_pb2 as pb


def group(s):
    if s.scene_membership is None:
        return ()
    return s.social_rooms.get(s.scene_membership, ())


def distance(a, b):
    return (a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2


def broadcast(s, msg, notice):
    raw = None
    for peer in group(s):
        if peer.running:
            if raw is None:
                import realm_uids
                raw = realm_uids.outgoing(notice).SerializeToString()
            peer.send(msg,raw,0,is_bin=True)


_multi_index = None


def multiplayer_actions():
    global _multi_index
    rows = res.index("Expression","expression")
    details = res.index("Expression","multiplayer_expression")
    if _multi_index is None or _multi_index[0] is not rows or _multi_index[1] is not details:
        actions = {g["action_i_d"]:details[g["multiplayer_i_d"]] for row in rows.values()
                   for g in row["expression_group_info"] if g.get("multiplayer_i_d") in details}
        _multi_index = (rows,details,actions)
    return _multi_index[2]


def invite(s, action, notice):
    # Menu animation IDs differ from the paired actor animation IDs.
    detail = multiplayer_actions().get(action)
    if detail and group(s):
        notice.multiple_need_count = detail["player_number"]
        notice.end_time = int(time.time()) + config("MultiplayerExpressionTimeout")
        set_after_commit(s,"social_invite",(action,detail,notice.end_time))
    elif s.social_invite:
        set_after_commit(s,"social_invite",None)


def accept(s, req, rsp):
    starter = next((p for p in group(s) if p.running and p.player_id == req.from_player_id),None)
    if starter is None or starter is s or not starter.social_invite:
        return pb.StatusCode_MULTIPLE_ACTION_INVALID
    action, detail, expiry = starter.social_invite
    if req.action_id not in (action,detail["starter_play_action_i_d"]) or time.time() >= expiry:
        return pb.StatusCode_MULTIPLE_ACTION_INVALID
    if req.status != pb.StatusCode_OK:
        return pb.StatusCode_OK
    a = s.scene_player.team.char1.pos
    b = starter.scene_player.team.char1.pos
    if distance(a,b) > (100*config("MultiplayerExpressionRange"))**2:
        return pb.StatusCode_MULTIPLE_ACTION_INVALID
    limit = detail.get("intimacy_limit",-1)
    if limit > 0 and (friend_points(s.player_id,starter.player_id) or 0) < limit:
        return pb.StatusCode_FRIEND_INTIMACY_NOT_ENOUGH
    notice = pb.SendMultipleActionCompleteNotice(status=pb.StatusCode_OK, action_id=action,
        from_player_id=starter.player_id, action_player_ids=[starter.player_id,s.player_id])
    s.commit_callbacks.append((setattr,(starter,"social_invite",None)))
    s.commit_callbacks.append((broadcast,(s,MsgId.SendMultipleActionCompleteNotice,notice)))
    return pb.StatusCode_OK


def facing(a,b):
    dot=a.x*b.x+a.y*b.y+a.z*b.z
    length=math.sqrt((a.x*a.x+a.y*a.y+a.z*a.z)*(b.x*b.x+b.y*b.y+b.z*b.z))
    return bool(length) and dot/length <= config("ClapDotProduct")/10


def clap(s, req, rsp):
    rsp.movement_id, rsp.is_cancel = req.movement_id, req.is_cancel
    if req.movement_id!=1061 or not group(s):  # eTogetherMovementID.HighFive in the native PC build.
        return pb.StatusCode_BAD_REQ
    if req.is_cancel:
        set_after_commit(s,"social_clap",None)
        return pb.StatusCode_OK
    if not req.HasField("position") or not req.HasField("forward"):
        return pb.StatusCode_BAD_REQ
    radius = (100*config("ClapDetectionRadius"))**2
    candidates = []
    for peer in group(s):
        if peer is s or not peer.running or peer.social_clap is None:
            continue
        pos, direction = peer.social_clap
        delta = distance(req.position,pos)
        if delta <= radius and facing(req.forward,direction):
            candidates.append((delta,peer.player_id,peer))
    if not candidates:
        set_after_commit(s,"social_clap",(req.position,req.forward))
        return pb.StatusCode_OK
    peer = min(candidates)[2]
    set_after_commit(s,"social_clap",None)
    s.commit_callbacks.append((setattr,(peer,"social_clap",None)))
    notice=pb.ClapResultNotice(status=pb.StatusCode_OK,movement_id=req.movement_id,
                               matched_player_id_lst=[peer.player_id,s.player_id])
    s.commit_callbacks.append((broadcast,(s,MsgId.ClapResultNotice,notice)))
    return pb.StatusCode_OK


def dice(s, req, rsp):
    if not group(s) or not req.dice_id or not req.HasField("position") or req.pips>6:
        return pb.StatusCode_BAD_REQ
    goods = res.index("Expression","expression")[1020]["expression_group_info"][0]
    costs,status = payments(s.player_id,{goods["need_item_i_d"]:goods["need_item_count"]})
    if status != pb.StatusCode_OK:
        return status
    save_pack(s,costs)
    stamp=time.monotonic()
    pips=req.pips or random.randint(1,6)
    current=(stamp,req.position,req.dice_id,pips)
    set_after_commit(s,"social_roll",current)
    notice=pb.ThrowDiceNotice(status=pb.StatusCode_OK,pips=pips,dice_id=req.dice_id,
                              position=req.position,player_name=s.player_name)
    s.commit_callbacks.append((broadcast,(s,MsgId.ThrowDiceNotice,notice)))
    radius=(100*config("DiceDetectionRadius"))**2
    players=[(s,current)]
    for peer in group(s):
        roll=peer.social_roll
        if peer is not s and peer.running and roll and 0<=stamp-roll[0]<=10 and distance(req.position,roll[1])<=radius:
            players.append((peer,roll))
    highest=max(v[3] for _,v in players)
    shared=sum(v[3]==highest for _,v in players)>1
    result=pb.ThrowDiceResultNotice(status=pb.StatusCode_OK)
    zone=result.zone_result_lst.add()
    for peer,(_,_,die,points) in sorted(players,key=lambda row:row[0].player_id):
        outcome=pb.ThrowDiceResult.Loss if points<highest else pb.ThrowDiceResult.Draw if shared else pb.ThrowDiceResult.Win
        zone.result_lst.add(dice_id=die,pips=points,won_result=outcome,
                            player_id=peer.player_id,player_name=peer.player_name)
    s.commit_callbacks.append((broadcast,(s,MsgId.ThrowDiceResultNotice,result)))
    return pb.StatusCode_OK
