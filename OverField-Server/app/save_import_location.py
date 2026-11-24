"""Resume the backed-up actor at its login scene, using the realm's public channel."""
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res

# First caption from String_Simplified.string_scene for each PC 1.1.12 free-roam scene.
NAMES={100:"瓶中小径",9999:"罗塔花园广场",1:"樱庭世界·昼",2:"贝海姆世界·正",3:"樱庭世界·夜",
       4:"贝海姆世界·逆",5:"谢尔盖蛋糕世界·蜜",1000:"舞台",1001:"水晶球",1002:"鸳鸯锅",1003:"星愿学园"}


def validate(value):
    if type(value) is not dict or "scene" not in value or set(value)-{"scene","pose","bottle_device_open"}:
        raise ValueError("invalid location")
    scene=value["scene"]
    if type(scene) is not int or not 0<scene<2**31:raise ValueError("invalid scene")
    out={"scene":scene}
    if "pose" in value:
        pose=value["pose"]
        if type(pose) is not dict or set(pose)!={"pos","rot"}:raise ValueError("invalid pose")
        for axes in pose.values():
            if type(axes) is not list or len(axes)!=3 or not all(type(i) is int and -(2**31)<=i<2**31 for i in axes):
                raise ValueError("invalid vector")
        out["pose"]=pose
    if "bottle_device_open" in value:
        if value["bottle_device_open"] is not True or scene!=100:raise ValueError("invalid prologue device")
        out["bottle_device_open"]=True
    if scene in {s["ID"] for s in res["ScenesConfigAsset"]["Scenes"] if s.get("Born")}:
        return out
    return None


def passed_plaza(plan):
    # The first objective can already be issued at the windmill, before the scene change.
    return (any(stories for _,stories in plan["chapters"]) or
            any(key!=100011 or state in (2,3) for key,state,_,_ in plan["quests"]))


def destination(plan):
    return plan.get("progress_location",{}).get("scene") or (9999 if passed_plaza(plan) else 100)


def summary(plan):
    scene=destination(plan)
    exact=plan.get("progress_location",{})
    placement=("采用备份中的角色位置" if exact.get("pose") else "采用场景出生点")
    if not exact:placement+="，备份未提供可恢复的登录场景"
    active=sorted(key for key,state,_,_ in plan["quests"] if state==pb.QuestStatus_InProgress)
    task="；进行中剧情任务 "+"、".join(map(str,active[:10]))+("等" if len(active)>10 else "") if active else ""
    return f"进度接续：{NAMES.get(scene,'场景 '+str(scene))}（{placement}）{task}。"


def restore(pid,plan):
    location=plan.get("progress_location",{})
    scene=destination(plan)
    db.conn.execute("UPDATE private_locations SET scene=? WHERE player_id=?",(scene,pid))
    if scene!=100 or passed_plaza(plan):
        db.conn.execute("UPDATE player_kv SET v='departed' WHERE player_id=? AND ns='profile' AND k='bottle_intro'",(pid,))
    if pose:=location.get("pose"):
        pos,rot=(pb.Vector3(x=x,y=y,z=z).SerializeToString() for x,y,z in (pose["pos"],pose["rot"]))
        db.conn.execute("INSERT OR REPLACE INTO private_poses VALUES(?,?,?,?)",(pid,scene,pos,rot))
    if location.get("bottle_device_open"):
        area=pb.AreaData(area_id=101,area_state=pb.AreaData.Unlock,level=1)
        db.set_area(pid,100,101,area.SerializeToString())
