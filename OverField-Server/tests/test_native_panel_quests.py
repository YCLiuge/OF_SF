"""Original commission and event dialogue acceptance, per-player prerequisites and reconnect state."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode
from local_identity import LocalIdentity
from private_first_quest import saved
from private_panel_quests import delegates
from private_shop import CN
from realm_events import available, configure
from test_game_realm import login, next_msg
from test_native_events import rpc, toggled
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("委托甲", "委托乙")]
    game = LocalGameService(identity, root, resources, port=0)
    clock = [datetime(2026, 11, 24, 12, tzinfo=CN)]
    pages = {row["id"]: row for row in available()}
    assert all(pages[k]["kind"] == "event_dialogue" for k in (5075, 260815130))

    def connect(index):
        sock, _, pid = login(game.port, identity.issue_ticket(*profiles[index]))
        main, early = rpc(sock, "PlayerMainData", 3)
        _, later = rpc(sock, "PlayerPing", 4)
        if main.scene_id == 100:
            assert call(sock, "ChangeSceneChannel", 5, scene_id=9999).status == pb.StatusCode_OK
        return sock, pid, main.quest_detail.quests, toggled(early + later, True)

    def accept(sock, qid, kind=pb.EQuestType_None):
        return call(sock, "AcceptQuest", 20, quest_id=qid, quest_type=kind)

    def move(sock, x, y, z):
        rec = pb.PlayerSceneRecordReq()
        pos = rec.data.char_recorder_data_lst.add(char_id=101001).pos
        pos.x, pos.y, pos.z = x, y, z
        sock.sendall(encode(MsgId.PlayerSceneRecordReq, rec.SerializeToString(), 21))
        next_msg(sock, MsgId.PlayerSceneSyncDataNotice, pb.PlayerSceneSyncDataNotice)

    def state(pid, key, status, progress):
        # Recreate prerequisites and a partially-completed older save without simulating unrelated objectives.
        with game.lock, db.conn:
            q = make_Quest(pid, key)
            q.status = q.conditions[0].status = status
            q.conditions[0].progress = progress
            db.set_quest(pid, key, q.SerializeToString())

    with patch("private_shop.now", side_effect=lambda: clock[0]):
        game.start()
        try:
            entries = delegates()
            assert len(entries) == 286 and len({npc for npc, row in entries.values()}) == 7
            a, pid, _, shown = connect(0)
            with a:
                assert not {5075, 260815130} & shown
                assert accept(a, 910101).status == pb.StatusCode_Activity_Close
                assert accept(a, 401001).status == pb.StatusCode_BAD_REQ
                move(a, -2538, 278, -2614)
                for key, kind in ((401001,8),(1000001,0),(910102,0),(401201,0),(401002,0)):
                    assert accept(a, key, kind).status == pb.StatusCode_BAD_REQ
                first = accept(a, 401001)
                assert first.status == pb.StatusCode_OK and first.quest.quest_id == 401001
                assert [(c.condition_id,c.progress) for c in first.quest.conditions] == [(14010001,0)]
                state(pid, 401001, pb.QuestStatus_InProgress, 3)
                assert accept(a, 401001).quest.conditions[0].progress == 3
                # All seven first commissions have a valid original trigger and configured condition set.
                for key, coords in ((401103,(141,-464,4044)),(401201,(2570,163,-2187)),
                                    (401301,(-1994,166,882)),(401401,(-738,143,2095)),
                                    (401501,(135,151,-2046)),(401601,(1992,183,-2666))):
                    move(a,*coords)
                    received = accept(a,key)
                    assert received.status == pb.StatusCode_OK and received.quest.quest_id == key
                    assert received.quest.status == pb.QuestStatus_InProgress and received.quest.conditions
            b, peer, quests, _ = connect(1)
            with b:
                assert peer != pid and not any(q.quest_id in entries for q in quests)
                move(b,-2538,278,-2614)
                with game.lock, db.conn:
                    db.conn.execute("UPDATE players SET level=4 WHERE player_id=?", (peer,))
                assert accept(b,401001).status == pb.StatusCode_BAD_REQ
                with game.lock, db.conn:
                    db.conn.execute("UPDATE players SET level=5 WHERE player_id=?", (peer,))
                assert accept(b,401001).quest.conditions[0].progress == 0
                assert accept(b,401002).status == pb.StatusCode_BAD_REQ
            game.stop()
            game.start()
            a, _, quests, _ = connect(0)
            with a:
                assert next(q for q in quests if q.quest_id == 401001).conditions[0].progress == 3
                move(a,-2538,278,-2614)
                state(pid,401001,pb.QuestStatus_Completed,10)
                assert accept(a,401002).status == pb.StatusCode_BAD_REQ
                state(pid,401001,pb.QuestStatus_Finish,10)
                assert accept(a,401001).status == pb.StatusCode_BAD_REQ
                # Scene 1 and quest_unlock_show gate locked previews. A qualified player can take the row.
                assert accept(a,401002).quest.quest_id == 401002
                assert accept(a,401006).status == pb.StatusCode_BAD_REQ
                state(pid,401005,pb.QuestStatus_Finish,1)
                assert saved(pid,401004) is None and accept(a,401006).quest.quest_id == 401006
                assert call(a,"ChangeSceneChannel",22,scene_id=1).status == pb.StatusCode_OK
                move(a,-2538,278,-2614)
                assert accept(a,401002).status == pb.StatusCode_BAD_REQ
            for index, aid, when in ((0,5075,(2025,10,25)),(1,260815130,(2026,11,1))):
                clock[0] = datetime(*when,12,tzinfo=CN)
                sock, _, quests, shown = connect(index)
                with sock:
                    assert {5075,260815130} & shown == {aid} and not any(q.quest_id == 910101 for q in quests)
                    q = accept(sock,910101)
                    assert q.status == pb.StatusCode_OK and q.quest.quest_id == 910101
                    assert [(c.condition_id,c.status) for c in q.quest.conditions] == [(19101011,pb.QuestStatus_InProgress)]
                    assert accept(sock,910101).quest == q.quest
            configure(root, {"events":[{"id":260815130,"enabled":False,"opens_at":None,"closes_at":None}]})
            sock, _, quests, shown = connect(1)
            with sock:
                assert 260815130 not in shown and any(q.quest_id == 910101 for q in quests)
                assert accept(sock,910101).status == pb.StatusCode_Activity_Close
            with game.lock:
                assert saved(peer,401001).conditions[0].progress == 0 and saved(peer,401002) is None
        finally:
            game.stop()
    print("PASS native NPC commissions, NPC proximity, level and prior quest gates, both event dialogue calendars, two characters and restart.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
