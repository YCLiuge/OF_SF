"""Accept and settle the stories that PC 1.1.12 actually lists in its chapter directory."""
from datetime import datetime
from collections import defaultdict

from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_Quest
from utils.res_loader import res
from private_dungeons import _table, grant, reward_items
from private_first_quest import save, saved
from private_map_progress import points as map_points
from private_shop import CN, now

_cached_sources = ()
_catalog = {}


def catalog():
    """Return story ID -> (chapter config, story config, authored ordered quest-step tuple)."""
    global _cached_sources, _catalog
    sources = (res["Story"], res["Quest"])
    if _cached_sources and all(a is b for a,b in zip(_cached_sources,sources)):
        return _catalog
    by_group = defaultdict(list)
    for step in sources[1]["quest"]["datas"]:
        by_group[step.get("quest_group")].append(step["i_d"])
    stories = _table("Story","story")
    result = {}
    for chapter in sources[0]["story_chapter"]["datas"]:
        for story_id in chapter["story_list"]:
            story = stories[story_id]
            steps = tuple(by_group[story["quest_i_d"]])
            if not steps or steps[0] != story["quest_i_d"] or story_id in result:
                raise ValueError("Inconsistent chapter quest group")
            result[story_id] = chapter, story, steps
    _cached_sources, _catalog = sources, result
    return result


def chapter_state(pid, chapter_id):
    blob = db.get_chapter(pid, chapter_id)
    return pb.Chapter.FromString(blob) if blob else pb.Chapter(chapter_id=chapter_id)


def meets_limits(pid, config, level, instant):
    if level < config.get("lv_unlock",0):
        return False
    when = config.get("time_unlock")
    if when and instant < datetime.fromisoformat(when).replace(tzinfo=CN):
        return False
    gate = config.get("map_completion_unlock")
    if gate:
        scene, percent = gate
        progress = _table("Scene","scene_progress").get(scene,{})
        total = sum(row.get("total",0) for row in progress.get("scene_progress_info",()))
        points = map_points(pid,scene)
        if total <= 0 or round(10000 * min(points,total) / total) < 100 * percent:
            return False
    return True


def unlocked(pid, chapter, story):
    level, = db.get_players_info(pid,"level")
    instant = now()
    if not all(meets_limits(pid,config,level,instant) for config in (chapter,story)):
        return False
    prior = chapter.get("pre_chapter_unlock")
    if prior:
        need = _table("Story","story_chapter")[prior]["story_list"]
        if not set(need).issubset(chapter_state(pid,prior).rewarded_story_ids):
            return False
    prior = story.get("pre_story_unlock")
    if prior:
        first = _table("Story","story")[prior]["quest_i_d"]
        quest = saved(pid,first)
        if not quest or quest.status == pb.QuestStatus_Locked:
            return False
    return True


def accept(session, req, rsp):
    entry = catalog().get(req.quest_id)
    if req.quest_type != pb.EQuestType_MainQuest or entry is None:
        return pb.StatusCode_BAD_REQ
    chapter, story, steps = entry
    pid = session.player_id
    # Resuming never reconstructs a saved objective or clears its completed conditions.
    for key in reversed(steps):
        quest = saved(pid,key)
        if quest and quest.status != pb.QuestStatus_Locked:
            rsp.quest.CopyFrom(quest)
            return pb.StatusCode_OK
    if req.quest_id in chapter_state(pid,chapter["i_d"]).rewarded_story_ids:
        return pb.StatusCode_REPEAT_GET_REWARD
    if not unlocked(pid,chapter,story):
        return pb.StatusCode_BAD_REQ
    quest = make_Quest(pid,steps[0])
    if not quest or not quest.conditions:
        return pb.StatusCode_CFG_NOT_FOUND
    save(session,quest)
    rsp.quest.CopyFrom(quest)
    return pb.StatusCode_OK


def claim(session, req, rsp):
    entry = catalog().get(req.quest_id)
    if req.quest_type != pb.EQuestType_MainQuest or entry is None:
        return pb.StatusCode_BAD_REQ
    chapter, story, steps = entry
    pid = session.player_id
    state = chapter_state(pid,chapter["i_d"])
    if req.quest_id in state.rewarded_story_ids:
        return pb.StatusCode_REPEAT_GET_REWARD
    for key in steps:
        quest = saved(pid,key)
        if quest is None or quest.status != pb.QuestStatus_Finish:
            return pb.StatusCode_BAD_REQ
    rewards = reward_items(pid,story["reward"])
    if not rewards:
        return pb.StatusCode_CFG_NOT_FOUND
    # Gameplay dispatch holds the realm lock and a single SQLite transaction around both writes.
    grant(session,rewards,rsp.items)
    state.rewarded_story_ids.append(req.quest_id)
    db.set_chapter(pid,chapter["i_d"],state.SerializeToString())
    rsp.quest.CopyFrom(quest)
    return pb.StatusCode_OK
