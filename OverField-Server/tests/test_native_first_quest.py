"""Use native frames against a temporary private realm to complete the first story, tutorial recruitment and reward claim."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import join_after_intro, login, next_msg


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    one = identity.create_profile("主线测试甲")
    two = identity.create_profile("主线测试乙")
    game = LocalGameService(identity, root, resources, port=0)

    def connect(profile):
        sock, _, player_id = login(game.port, identity.issue_ticket(*profile))
        main, _ = join_after_intro(sock)
        states = {r.quest_id:r.status for r in main.quest_detail.quests}
        return sock, player_id, states

    def call(sock, name, **fields):
        req = getattr(pb, name+"Req")(**fields)
        sock.sendall(encode(getattr(MsgId,name+"Req"),req.SerializeToString(),17))
        updates = []
        for _ in range(160):
            head,raw = receive(sock)
            if head.msg_id == MsgId.QuestNotice:
                updates.extend(pb.QuestNotice.FromString(raw).quests)
            elif head.msg_id == getattr(MsgId,name+"Rsp") and head.packet_id == 17:
                return getattr(pb,name+"Rsp").FromString(raw), {q.quest_id:q.status for q in updates}
        raise AssertionError("No reply")

    def move(sock,x,z):
        req = pb.PlayerSceneRecordReq()
        record = req.data.char_recorder_data_lst.add(char_id=101001)
        record.pos.x,record.pos.z = x,z
        sock.sendall(encode(MsgId.PlayerSceneRecordReq,req.SerializeToString(),18))
        echo = next_msg(sock,MsgId.PlayerSceneSyncDataNotice,pb.PlayerSceneSyncDataNotice)
        assert echo.data[0].data[0].char_recorder_data_lst[0].pos.x == x

    def owned(item,player):
        with closing(sqlite3.connect(root/"game.sqlite")) as conn:
            row=conn.execute("SELECT item_detail_blob FROM items WHERE item_id=? AND player_id=?",(item,player)).fetchone()
            return pb.ItemDetail.FromString(row[0]).main_item.base_item.num if row else 0

    def accept(sock):
        return call(sock,"AcceptQuest",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)[0].quest

    game.start()
    try:
        c, pid, progress = connect(one)
        with c:
            assert 100011 not in progress
            assert accept(c).quest_id == 100011
            assert call(c,"UpdateTeam",char1=102003)[0].status==pb.StatusCode_CharNotExist
            assert 2000 in {g.gacha_id for g in call(c,"GachaList")[0].gachas}
            assert call(c,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)[0].status==pb.StatusCode_BAD_REQ
            assert call(c,"NpcTalk",id=11000111)[0].status == pb.StatusCode_BAD_REQ
            assert call(c,"NpcTalk",id=11000121)[0].status == pb.StatusCode_BAD_REQ
            move(c,1992,-2666)
            rsp,states = call(c,"NpcTalk",id=11000111)
            assert rsp.status==pb.StatusCode_OK and states=={100011:3,100012:1}
            assert call(c,"NpcTalk",id=11000111)[1]=={}
        c, same, progress = connect(one)
        with c:
            assert pid==same and progress[100011]==3 and progress[100012]==1
            assert accept(c).quest_id==100012
            assert call(c,"Tutorial",tutorial_id=12,tutorial_name="菲对话结束")[0].status==pb.StatusCode_OK
            assert call(c,"Tutorial",tutorial_id=11,tutorial_name="菲对话开始")[0].status==pb.StatusCode_OK
            assert call(c,"NpcTalk",id=11000121)[0].status==pb.StatusCode_BAD_REQ
            move(c,2570,-2187)
            before = {k:owned(k,pid) for k in (104,100000007)}
            rsp, states = call(c,"NpcTalk",id=11000121)
            assert rsp.status==pb.StatusCode_OK and states=={100012:3,100013:1}
            assert call(c,"NpcTalk",id=11000121)[1]=={}
            assert {k:owned(k,pid)-v for k,v in before.items()}=={104:1,100000007:1}
            assert call(c,"NpcTalk",id=11000131)[0].status==pb.StatusCode_BAD_REQ
            assert {2000, 3000, 4000, 9000} <= {g.gacha_id for g in call(c,"GachaList")[0].gachas}
            assert call(c,"Gacha",gacha_id=1000,is_single=True)[0].status==pb.StatusCode_BAD_REQ
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                old_ticket = db.execute("SELECT item_detail_blob FROM items WHERE player_id=? AND item_id=104",(pid,)).fetchone()[0]
                db.execute("DELETE FROM items WHERE player_id=? AND item_id=104",(pid,))
                db.commit()
            assert call(c,"Gacha",gacha_id=2000,is_single=True)[0].status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert not call(c,"GachaRecord",gacha_id=2000,page=1)[0].records
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                db.execute("INSERT INTO items(player_id,item_id,item_detail_blob) VALUES (?,104,?)",(pid,old_ticket))
                db.commit()
            starting_ticket = owned(104,pid)
            starting_bonus = owned(107,pid)
            drawn,updates = call(c,"Gacha",gacha_id=2000,is_single=True)
            assert drawn.status==pb.StatusCode_OK and drawn.info.gacha_times==1 and updates=={100013:3}
            assert len(drawn.items)==1
            card=drawn.items[0]
            assert not card.main_item.is_new and card.main_item.character.character_id==102001
            assert card.main_item.item_tag==pb.EBagItemTag_Card and card.extra_quality==3
            assert any(x.item_id==107 and x.base_item.num==500 for x in card.extras)
            with closing(sqlite3.connect(root/"game.sqlite")) as ledger:
                receipt = ledger.execute("SELECT r.origin,r.is_new,r.extras FROM private_gacha_rewards r "+
                    "JOIN gacha_record g ON g.id=r.record_id WHERE g.player_id=?", (pid,)).fetchall()
                assert len(receipt)==1 and receipt[0][:2]==("tutorial",0) and [107,500] in __import__("json").loads(receipt[0][2])
            assert owned(104,pid)==starting_ticket-1 and owned(107,pid)==starting_bonus+500
            assert call(c,"Gacha",gacha_id=2000,is_single=True)[0] == drawn
            assert len(call(c,"GachaList")[0].gachas) > 1
            assert owned(104,pid)==starting_ticket-1 and owned(107,pid)==starting_bonus+500
            assert call(c,"UpdateTeam",char1=102001,char2=102001)[0].status==pb.StatusCode_BAD_REQ
            assert call(c,"UpdateTeam",char2=101001)[0].status==pb.StatusCode_BAD_REQ
            def current_team():
                for _ in range(100):
                    sync=next_msg(c,MsgId.ServerSceneSyncDataNotice,pb.ServerSceneSyncDataNotice)
                    for entry in sync.data:
                        for event in entry.server_data:
                            if entry.player_id==pid and event.action_type==pb.SceneActionType_UPDATE_TEAM:
                                return event.player.team
                raise AssertionError("No team update")
            assert call(c,"UpdateTeam",char1=102001,char2=101001)[0].status==pb.StatusCode_OK
            team=current_team()
            assert (team.char1.char_id,team.char1.weapon_id,team.char2.char_id)==(102001,1102101,101001)
            assert (team.char1.pos.x,team.char1.pos.z)==(2570,-2187)
            assert call(c,"UpdateTeam",char1=102001)[0].status==pb.StatusCode_OK
            assert not current_team().HasField("char2")
            hist=call(c,"GachaRecord",gacha_id=2000,page=1)[0]
            assert hist.total_page==1 and [r.item_id for r in hist.records]==[102001]
            assert not call(c,"GachaRecord",gacha_id=2000,page=2)[0].records
            pre_claim={k:owned(k,pid) for k in (1,101,102)}
            assert call(c,"Tutorial",tutorial_id=23,tutorial_name="引导任务奖励领取")[0].status==pb.StatusCode_OK
            assert {k:owned(k,pid) for k in pre_claim}==pre_claim
            claimed,_=call(c,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)
            assert claimed.status==pb.StatusCode_OK and claimed.quest.quest_id==100013 and claimed.quest.complete_count==1
            obtained={i.main_item.item_id:i.main_item.base_item.num for i in claimed.items}
            assert obtained[1]==300 and obtained[101]==300 and obtained[102]==50, obtained
            post_claim={k:owned(k,pid) for k in obtained}
            assert {k:post_claim[k]-pre_claim[k] for k in pre_claim}=={1:300,101:300,102:50}
            # Actual original guide reports around the buttons on the chapter reward page.
            reports = [(13,"梓询问招募情况开始"), (14,"梓询问招募情况结束"),
                       (15,"提交招募申请"), (16,"提交招募申请对话结束"),
                       (17,"引导招募icon"), (18,"引导招募单抽"), (19,"诗对话开始"), (20,"诗对话结束"),
                       (21,"点击引导剧情"), (22,"点击引导剧情序章"),
                       (23,"引导任务奖励领取"), (24,"引导接受任务")]
            for phase,label in reports:
                assert call(c,"Tutorial",tutorial_id=phase,tutorial_name=label)[0].status==pb.StatusCode_OK
            assert call(c,"Tutorial",tutorial_id=23,tutorial_name="unknown")[0].status==pb.StatusCode_BAD_REQ
            assert call(c,"Tutorial",tutorial_id=25)[0].status==pb.StatusCode_BAD_REQ
            assert call(c,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)[0].status==pb.StatusCode_REPEAT_GET_REWARD
            assert {k:owned(k,pid) for k in obtained}==post_claim
        other, _, states = connect(two)
        with other:
            assert not any(k in states for k in (100011,100012,100013))
            assert call(other,"Tutorial",tutorial_id=23)[0].status==pb.StatusCode_BAD_REQ
            assert call(other,"UpdateTeam",char1=102003)[0].status==pb.StatusCode_CharNotExist
            assert not call(other,"GachaRecord",gacha_id=2000,page=1)[0].records
            assert call(other,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)[0].status==pb.StatusCode_BAD_REQ
        # A full service restart restores the roster, equipment, story claim and recruitment history.
        game.stop()
        game.start()
        c, _, same = login(game.port,identity.issue_ticket(*one))
        with c:
            main,_=join_after_intro(c)
            for phase,label in reports:
                assert call(c,"Tutorial",tutorial_id=phase,tutorial_name=label)[0].status==pb.StatusCode_OK
            with closing(sqlite3.connect(root/"game.sqlite")) as ledger:
                recorded = ledger.execute("SELECT k FROM player_kv WHERE player_id=? AND ns='tutorial'",(pid,))
                assert {str(phase) for phase,_ in reports} <= {key for key, in recorded}
            assert {k:owned(k,pid) for k in obtained}==post_claim
            assert pid==same and (main.team.char1,main.team.char2,main.team.char3)==(102001,0,0)
            assert {q.quest_id:q.status for q in main.quest_detail.quests if q.quest_id in (100011,100012,100013)}=={100011:3,100012:3,100013:3}
            chapter=next(ch for ch in main.quest_detail.chapters if ch.chapter_id==10000)
            assert list(chapter.rewarded_story_ids).count(1000001)==1
            shi=next(ch for ch in main.characters if ch.character_id==102001)
            instance=shi.equipment_presets[0].weapon
            with closing(sqlite3.connect(root/"game.sqlite")) as db:
                raw=db.execute("SELECT item_detail_blob FROM items_s WHERE player_id=? AND instance_id=?",(pid,instance)).fetchone()[0]
            weapon=pb.ItemDetail.FromString(raw).main_item.weapon
            assert weapon.weapon_id==1102101 and weapon.wearer_id==102001
            assert [r.item_id for r in call(c,"GachaRecord",gacha_id=2000,page=1)[0].records]==[102001]
            assert call(c,"ClaimQuestReward",quest_id=1000001,quest_type=pb.EQuestType_MainQuest)[0].status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        game.stop()
    print("PASS native first-story accept, 2 NPC regions, tutorial draw, native roster/equipment, team change, history, story rewards, isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
