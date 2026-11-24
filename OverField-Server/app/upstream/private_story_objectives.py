"""Single-condition Chapter 00 objectives backed by actual gameplay, including old-save catch-up."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import _table
from private_first_quest import save, saved
from private_map_progress import catalog as map_catalog, points
from utils.pb_create import make_Quest

# All five objectives have exactly one configured goal and no immediate item reward.
OBJECTIVES = {100032:(11000321,44,100033), 100034:(11000341,40,100035),
              100035:(11000351,40,None), 100086:(11000861,168,100087),
              100093:(11000931,113,100094)}
MECHANISMS = {1000341:100034, 1000351:100035}


def _record(session, step, value, notify=True):
    aid, kind, next_step = OBJECTIVES[step]
    q = saved(session.player_id,step)
    if (q is None or q.status != pb.QuestStatus_InProgress or len(q.conditions)!=1
            or q.conditions[0].condition_id!=aid or q.conditions[0].status!=pb.QuestStatus_InProgress):
        return False
    rule = _table("Achieve","achieve")[aid]
    if rule.get("new_achieve_condition")!=kind or rule.get("count_param",0)<=0:
        return False
    c = q.conditions[0]
    progress = min(value,rule["count_param"])
    if progress <= c.progress:
        return False
    # Never overwrite an already-started successor if an old save contains inconsistent states.
    if next_step and saved(session.player_id,next_step) is not None:
        return False
    c.progress = progress
    completed = progress == rule["count_param"]
    if completed:
        c.status = q.status = pb.QuestStatus_Finish
        q.complete_count = 1
    save(session,q)
    notice = pb.QuestNotice(status=pb.StatusCode_OK,quests=[q])
    if completed and next_step:
        successor = make_Quest(session.player_id,next_step)
        save(session,successor)
        notice.quests.add().CopyFrom(successor)
    if notify:
        session.send(MsgId.QuestNotice,notice,0)
    return True


def at_fount(session):
    # The plaza NPC's interaction BoxCollider; the larger quest-marker sphere is separate.
    if session.scene_id!=9999 or session.dungeon[0]:
        return False
    pos=session.scene_player.team.char1.pos
    dx,dz=pos.x/100+25.38093,pos.z/100+26.14471
    return (abs(0.74073431*dx-0.67179809*dz)<=1.991454
            and abs(0.67179809*dx+0.74073431*dz)<=2.670020 and pos.y/100<=4.58632)


def learned(session, notify=True):
    from private_badge_tree import _owned
    if saved(session.player_id,100032) is None:
        return
    rule = _table("Achieve","achieve")[11000321]
    if rule.get("param")==[101000000] and _owned(session.player_id,101000000):
        _record(session,100032,1,notify)


def explored(session, scene=1, notify=True):
    if scene!=1 or saved(session.player_id,100093) is None:
        return
    rule = _table("Achieve","achieve")[11000931]
    total = sum(map_catalog()[0].get(scene,{}).values())
    if rule.get("param")==[scene] and total:
        _record(session,100093,10000*points(session.player_id,scene)//total,notify)


def cooked(session):
    _record(session,100086,1)


def mechanism(session, req, rsp):
    step = MECHANISMS.get(req.id)
    if step is None:
        return False
    rsp.status = pb.StatusCode_BAD_REQ
    if req.talk_type != pb.NpcTalkReq.NpcTalkType_Mechanism or session.scene_id != 1 or session.dungeon[0]:
        return True
    before=saved(session.player_id,step-1)
    current=saved(session.player_id,step)
    if not before or before.status!=pb.QuestStatus_Finish or not current:
        return True
    if current.status==pb.QuestStatus_Finish:
        rsp.status=pb.StatusCode_OK
        return True
    if step==100034:
        # Event1000341 checks entry into a 4 x 1 x 4 box, at the destination's height.
        pos=session.scene_player.team.char1.pos
        dx,dy,dz=pos.x/100+5.17,pos.y/100-9.11,pos.z/100-31.79
        y,w=0.99981141,-0.01942035
        co,si=1-2*y*y,2*w*y
        if max(abs(dx*co-dz*si),abs(dx*si+dz*co))>2 or abs(dy)>1:
            return True
    # Event1000351 listens for eInteractionGlideJump; this callback has no location restriction.
    from private_badge_tree import _owned
    if _owned(session.player_id,101000000) and _record(session,step,1):
        rsp.status=pb.StatusCode_OK
    return True
