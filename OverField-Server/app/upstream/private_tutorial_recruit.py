"""Native tutorial recruitment and first-story completion for a private realm."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item
from utils.res_loader import res
from private_dungeons import grant, reward_items
from private_first_quest import CONDITIONS, STEPS, STORY, save, saved

POOL = 2000
SHI = 102001


def row(resource, table, row_id):
    return next(r for r in res[resource][table]["datas"] if r["i_d"] == row_id)


def pool_info(pid):
    config = row("Gacha", "info", POOL)
    return pb.GachaInfo(gacha_id=POOL, gacha_times=db.get_total_gacha_num(pid, POOL),
                        guarantee=max(0, (config.get("max_times") or 70) - db.get_gacha_guarantee(pid, POOL)))


def list_pools(session, req, rsp):
    quest = saved(session.player_id, STEPS[-1])
    if quest and quest.status == pb.QuestStatus_InProgress:
        rsp.gachas.add().CopyFrom(pool_info(session.player_id))
    return pb.StatusCode_OK


def draw(session, req, rsp):
    pid = session.player_id
    if req.gacha_id != POOL or not req.is_single or session.dungeon[0] or session.scene_id != 9999:
        return pb.StatusCode_BAD_REQ
    quest = saved(pid, STEPS[-1])
    if not quest:
        return pb.StatusCode_BAD_REQ
    if quest.status == pb.QuestStatus_Finish:
        return pb.StatusCode_REPEAT_GET_REWARD
    if (quest.status != pb.QuestStatus_InProgress or len(quest.conditions) != 1
            or quest.conditions[0].condition_id != CONDITIONS[-1]):
        return pb.StatusCode_BAD_REQ
    config = row("Gacha", "info", POOL)
    ticket_id = config["consume_item1_i_d"]
    cost = config["consume_item1_num"]
    raw = db.get_item_detail(pid, ticket_id)
    ticket = pb.ItemDetail.FromString(raw) if raw else None
    if not ticket or ticket.main_item.WhichOneof("item") != "base_item" or ticket.main_item.base_item.num < cost:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    ticket.main_item.base_item.num -= cost
    db.set_item_detail(pid, ticket.SerializeToString(), ticket_id)

    item = rsp.items.add()
    item.main_item.item_id = SHI
    item.main_item.item_tag = pb.EBagItemTag_Card
    owned = db.get_characters(pid, SHI)
    item.main_item.is_new = not bool(owned)
    item.extra_quality = row("Item", "item", SHI)["quality"]
    if owned:
        item.main_item.character.ParseFromString(owned[0])
        character = item.main_item.character
    else:
        character = db.add_character(pid, SHI, item.main_item.character)
    bonus = make_item(107, 500, pid)
    item.extras.add().CopyFrom(bonus.main_item)
    grant(session, {107: 500}, pb.GachaRsp().items)
    notice = pb.PackNotice(status=pb.StatusCode_OK)
    notice.items.extend([ticket, item])
    if not owned:
        weapon_blob = db.get_item_detail(pid, instance_id=character.equipment_presets[0].weapon)
        notice.items.add().ParseFromString(weapon_blob)
        outfit = character.outfit_presets[0]
        for item_id in (outfit.hat, outfit.hair, outfit.clothes, outfit.ornament):
            if item_id:
                notice.items.add().ParseFromString(db.get_item_detail(pid, item_id))
    db.add_gacha_record(pid, POOL, SHI)
    db.set_gacha_guarantee(pid, POOL, db.get_gacha_guarantee(pid, POOL) + 1)
    rsp.info.CopyFrom(pool_info(pid))
    quest.conditions[0].progress = quest.complete_count = 1
    quest.conditions[0].status = quest.status = pb.QuestStatus_Finish
    save(session, quest)
    session.send(MsgId.PackNotice, notice, 0)
    session.send(MsgId.QuestNotice, pb.QuestNotice(status=pb.StatusCode_OK, quests=[quest]), 0)
    return pb.StatusCode_OK


def history(session, req, rsp):
    if req.gacha_id != POOL or req.page < 1:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    rsp.total_page = (db.get_total_gacha_num(pid, POOL) + 4) // 5
    for item_id, when in db.get_gacha_records(pid, POOL, req.page):
        rsp.records.add(gacha_id=POOL, item_id=item_id, gacha_time=when)
    return pb.StatusCode_OK


def claim_story(session, req, rsp):
    if req.quest_id != STORY or req.quest_type != pb.EQuestType_MainQuest:
        return pb.StatusCode_BAD_REQ
    pid = session.player_id
    quests = [saved(pid, step) for step in STEPS]
    if any(q is None or q.status != pb.QuestStatus_Finish for q in quests):
        return pb.StatusCode_BAD_REQ
    chapter_id = next(ch["i_d"] for ch in res["Story"]["story_chapter"]["datas"] if STORY in ch["story_list"])
    raw = db.get_chapter(pid, chapter_id)
    chapter = pb.Chapter.FromString(raw) if raw else pb.Chapter(chapter_id=chapter_id)
    if STORY in chapter.rewarded_story_ids:
        return pb.StatusCode_REPEAT_GET_REWARD
    rewards = reward_items(pid, row("Story", "story", STORY)["reward"])
    if not rewards:
        return pb.StatusCode_CFG_NOT_FOUND
    grant(session, rewards, rsp.items)
    chapter.rewarded_story_ids.append(STORY)
    db.set_chapter(pid, chapter_id, chapter.SerializeToString())
    rsp.quest.CopyFrom(quests[-1])
    return pb.StatusCode_OK
