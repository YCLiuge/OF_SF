"""Chapter 00 scenes and quest interactions authored by the PC 1.1.12 field prefabs."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from private_dungeons import reward_items
from private_first_quest import saved
from private_shop import award
from utils.pb_create import make_QuestNotice

# (quest, condition, position, half width, half height, object yaw). Button boxes use
# 4x2x4; automatic triggers use each prefab's authored dimensions. Type 100 is
# the completed rewind action and can arrive at the destination of the rewind.
YAW = (-0.9992457,-0.0388334)
ALMOST_SOUTH = (-0.999998,-0.002)
MECHANISMS = {
    1000411:(100041,11000411,(-21.65,0.8789,39.08),1500,1500,YAW),
    1000421:(100042,11000421,(-43.9,2.68,30.49),1.5,1.5,YAW),
    1000451:(100043,11000431,None,0,0,YAW),
    1000431:(100044,11000441,(-37.1614,-0.1893,5.7313),2,1,YAW),
    1000441:(100045,11000451,(-30.66,-0.1878,2.0121),2,1,YAW),
    1000532:(100053,11000532,(-19.9739,-0.1853,-37.7519),2,1,ALMOST_SOUTH),
    1000541:(100054,11000541,(-48.2521,9.5533,-75.3795),2,0.5,YAW),
    1000731:(100073,11000731,(45.4,7.02,-73.83),3,7,(1,0)),
    1000732:(100073,11000732,(-1.07,5.49,77.755),2.5,4.5,(1,0)),
    1000733:(100073,11000733,(-70.66,7.09,73.12),2.5,8,(1,0)),
    1000751:(100075,11000751,(-6.8191,42.5859,-29.9788),2.5,2,(1,0)),
    1000831:(100083,11000831,(42.89,-5.8587,11.66),2,1,YAW),
    1000891:(100089,11000891,(26.5401,-5.9428,52.0275),2,1,(-0.026,0.99966)),
    1000901:(100090,11000901,(26.5401,-5.9428,52.0275),1500,1500,(-0.026,0.99966)),
    1000981:(100098,11000981,(26.5401,-5.9428,52.0275),1500,1500,(-0.026,0.99966)),
    1000631:(100063,11000631,(40.47,0.0635,-21.15),2,1,YAW),
}
DIALOGUES = {11000511:(100051,10005), 11000551:(100055,10005),
             11000611:(100061,10006), 11000661:(100066,10006),
             11000711:(100071,10005), 11000811:(100081,10006), 11000821:(100082,10002),
             11000841:(100084,10001), 11000851:(100085,10008), 11000871:(100087,10008),
             11000881:(100088,10005), 11000911:(100091,10005), 11000941:(100094,10005)}
# Deterministic fixed rewards at these steps, including a rolled inventory instance in 654.
STEP_REWARDS = {100051:665,100063:671,100064:654,100085:656,100098:667,100094:652}


def step_reward(session, row):
    if row["i_d"] not in STEP_REWARDS or row.get("reward") != STEP_REWARDS[row["i_d"]]:
        return
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    award(session.player_id, reward_items(session.player_id,row["reward"]), pb.ShopBuyRsp(), pack)
    session.send(MsgId.PackNotice,pack,0)


def inside(pos, center, half, half_height, yaw):
    if center is None:
        return True
    dx, dy, dz = pos.x/100-center[0], pos.y/100-center[1], pos.z/100-center[2]
    co,si = yaw
    # XZ comes from the stored feet position. Allow overlap with a standing avatar vertically.
    return (max(abs(dx*co-dz*si), abs(dx*si+dz*co)) <= half
            and -half_height-1.0 <= dy <= half_height)


def talk(session, req, rsp):
    if req.id in MECHANISMS:
        step, condition, center, half, height, yaw = MECHANISMS[req.id]
        map_id = 9999 if 100081<=step<=100098 else 1
        valid = (req.talk_type == pb.NpcTalkReq.NpcTalkType_Mechanism and session.scene_id == map_id
                 and inside(session.scene_player.team.char1.pos,center,half,height,yaw))
    elif req.id in DIALOGUES:
        step, actor = DIALOGUES[req.id]
        condition = req.id
        if actor != 10006:
            from private_panel_quests import _near_actor
            at_actor = _near_actor(session,actor)
        else:
            from private_story_dialogues import CENTER_Y, RADIUS, X, Z
            point = session.scene_player.team.char1.pos
            at_actor = ((point.x/100-X)**2 + (point.z/100-Z)**2 <= RADIUS**2
                        and point.y/100 <= CENTER_Y+RADIUS)
        valid = req.talk_type == pb.NpcTalkReq.NpcTalkType_Npc and session.scene_id == 9999 and at_actor
    else:
        return False
    rsp.status = pb.StatusCode_BAD_REQ
    if not valid or session.dungeon[0]:
        return True
    quest = saved(session.player_id,step)
    if not quest:
        return True
    if (quest.status == pb.QuestStatus_Finish or any(c.condition_id == condition and
            c.status == pb.QuestStatus_Finish for c in quest.conditions)):
        # A repeated delivery after the next step began must never repeat the reward.
        rsp.status = pb.StatusCode_OK
        return True
    notice = make_QuestNotice(session,(condition,))
    if notice:
        rsp.status = pb.StatusCode_OK
        session.send(MsgId.QuestNotice,notice,0)
    return True
