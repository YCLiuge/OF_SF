from network.packet_handler import PacketHandler, packet_handler
from network.msg_id import MsgId
import logging

from proto.net_pb2 import (
    PlayerMainDataRsp,
    StatusCode,
    PackNotice,
    SceneDataNotice,
    ChatMsgRecordInitNotice,
    ChangeChatChannelRsp,
    QuestStatus,
    RandomQuestBonus,
    PlayerBuff,
    PlayerQuestionnaireInfo,
    ItemDetail,
    ServerSceneSyncDataNotice,
    SceneActionType,
    PetWarehouseLimitNotice,
)
import utils.db as db
from utils.res_loader import res
from utils.pb_create import make_SceneDataNotice, make_ScenePlayer
# Local-host branch: no cross-server forwarding.
import server.notice_sync as notice_sync
from server.scene_data import up_scene_action

logger = logging.getLogger(__name__)


@packet_handler(MsgId.PlayerMainDataReq)
class Handler(PacketHandler):
    def handle(self, session, data: bytes, packet_id: int):
        player_id = session.player_id

        rsp = PlayerMainDataRsp()
        rsp.status = StatusCode.StatusCode_OK
        rsp.player_id = session.player_id

        infos = db.get_players_info(
            player_id,
            "player_name,unlock_functions,level,exp,head,sign,sex,avatar_frame,pendant,world_level,phone_background,create_time,team,birthday,is_hide_birthday,account_type,pet_instance_id",
        )
        configured = [row["i_d"] for row in res["PlayerUnlock"]["player_unlock"]["datas"]]
        unlocks = list(dict.fromkeys([*(infos[1] or ()), *configured]))
        if unlocks != infos[1]:
            db.set_players_info(player_id, "unlock_functions", unlocks)
        rsp.unlock_functions.extend(unlocks)
        (
            rsp.player_name,
            _,
            rsp.level,
            rsp.exp,
            session.avatar_id,
            rsp.sign,
            rsp.sex,
            rsp.appearance.avatar_frame,
            rsp.appearance.pendant,
            rsp.world_level,
            rsp.phone_background,
            rsp.create_time,
            [rsp.team.char1, rsp.team.char2, rsp.team.char3],
            rsp.birthday,
            rsp.is_hide_birthday,
            rsp.account_type,
            rsp.pet_instance_id,
        ) = infos
        from private_garden_residents import own_residents
        own_residents(player_id,rsp.placed_characters)
        rsp.hide_value, = db.get_players_info(player_id, "hide_value")
        rsp.head = session.avatar_id
        session.player_name = rsp.player_name
        logger.info(f"Player login: {session.player_name}({session.player_id})")
        make_ScenePlayer(session)
        # Cross-server publication is disabled.

        from private_character_training import import_skills
        import_skills(player_id)
        for chr in db.get_characters(player_id):
            rsp.characters.add().ParseFromString(chr)

        rsp.scene_id = session.scene_id
        rsp.channel_id = session.channel_id

        for chapter in db.get_chapter(session.player_id):
            rsp.quest_detail.chapters.add().ParseFromString(chapter)
        rsp.quest_detail.random_quest_bonus_left.CopyFrom(RandomQuestBonus())
        from private_story_objectives import learned, explored
        learned(session,notify=False)
        explored(session,notify=False)
        from private_bless_tasks import ensure as prayer_tasks,catalog as prayer_catalog
        daily=prayer_tasks(session,repair_on_login=not session.logged_in)
        prayer_definitions=prayer_catalog()
        rsp.daily_task.tasks.update(dict(enumerate(daily,1)))
        for quest in db.get_quest(session.player_id):
            tmp = rsp.quest_detail.quests.add()
            tmp.ParseFromString(quest)
            if tmp.quest_id in prayer_definitions and tmp.quest_id not in daily:
                del rsp.quest_detail.quests[-1]
                continue
            if tmp.status == QuestStatus.QuestStatus_InProgress:
                session.quests[tmp.quest_id] = tmp

        rsp.player_buffs.add().system_type = PlayerBuff.BuffSystemType_GLOBAL
        from private_outfit_dye import fill_login
        fill_login(player_id, rsp)
        rsp.player_drop_rate_info.kill_drop_rate = 1000
        rsp.player_drop_rate_info.treasure_drop_rate = 1000
        rsp.questionnaire_info.CopyFrom(PlayerQuestionnaireInfo())
        rsp.player_label = player_id
        rsp.channel_label = session.channel_id
        rsp.month_card_over_due_time = db.get_month_card_over_due_time(player_id)
        rsp.garden_likes_num, _, _, _, _ = db.get_garden_info(player_id)
        rsp.month_card_reward_days = db.get_month_card_reward_days(player_id)
        from private_arcade_wallet import snapshot, recover
        snapshot(session,rsp.daily_task)
        recover(session)
        from private_appearance import phone_case
        rsp.phone_case=phone_case(player_id)
        rsp.sp_open_status = True
        rsp.sp_check_status = True
        session.send(MsgId.PlayerMainDataRsp, rsp, packet_id)  # 1005,1006

        from private_social_profile import daily, gift_count
        gift_count(session, daily(player_id)[1])

        from private_recovery import settle_vitality
        settle_vitality(player_id)

        from private_bless_tree import login_notice as bless_tree_notice
        tree_progress = bless_tree_notice(player_id)
        items_data = db.get_item_detail(session.player_id)
        if items_data:
            items_by_tag = {}

            for item_blob in items_data:
                item = ItemDetail()
                item.ParseFromString(item_blob)
                if item.main_item.HasField("treasure"):
                    from private_treasure_dig import repair
                    repair(player_id, item)
                item_tag = item.main_item.item_tag
                if item_tag not in items_by_tag:
                    items_by_tag[item_tag] = []
                items_by_tag[item_tag].append(item)

            for item_tag, items_in_tag in items_by_tag.items():
                rsp = PackNotice()
                rsp.status = StatusCode.StatusCode_OK
                for item in items_in_tag:
                    # TODO 临时物品发到邮件
                    rsp.items.add().CopyFrom(item)
                session.send(
                    MsgId.PackNotice, rsp, 0, inventory_snapshot=True
                )  # 按物品类型分组发送items物品数据

        from private_inventory import slots, publish
        publish(session, slots(player_id).values(), clear=True)

        from private_activity_rewards import login as activity_sign_in
        activity_sign_in(session)

        session.send(MsgId.BlessTreeNotice, tree_progress, 0)

        rsp = SceneDataNotice()
        rsp.CopyFrom(make_SceneDataNotice(session))
        session.send(MsgId.SceneDataNotice, rsp, 0)

        from private_chat import login as init_chat
        init_chat(session,packet_id)
        from private_entitlements import notify as notify_collections
        notify_collections(session)

        rsp = PetWarehouseLimitNotice()
        rsp.status = StatusCode.StatusCode_OK
        from private_pets import capacity
        rsp.warehouse_limit = capacity(player_id)
        session.send(MsgId.PetWarehouseLimitNotice, rsp, packet_id)

        # 视为完成登录，同步场景玩家并广播加入事件
        if session.logged_in == False:
            session.logged_in = True
            from private_mp_teams import on_login as mp_login
            mp_login(session)
            from private_wishes import deliver
            deliver(session)

            notice = ServerSceneSyncDataNotice()
            notice.status = StatusCode.StatusCode_OK
            d = notice.data.add()
            d.player_id = session.player_id
            sd = d.server_data.add()
            sd.action_type = SceneActionType.SceneActionType_ENTER
            sd.player.CopyFrom(session.scene_player)
            up_scene_action(session.scene_id, session.channel_id, notice)

            # 同步时间
            rsp = ServerSceneSyncDataNotice()
            rsp.status = StatusCode.StatusCode_OK
            tmp = rsp.data.add().server_data.add()
            tmp.action_type = SceneActionType.SceneActionType_TOD_UPDATE
            tmp.tod_time = int(notice_sync.tod_time)
            session.send(MsgId.ServerSceneSyncDataNotice, rsp, 0)
