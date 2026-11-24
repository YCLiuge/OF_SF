"""Train six professions through real realm sockets, including paid speed-up, restart and garden quota."""
import argparse
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_crafts import base
from private_shop import balance
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("技能甲", "技能乙")]
    game = LocalGameService(identity, root, resources, port=0)
    epoch = 1760000000
    now = [epoch]
    ok = pb.StatusCode_OK
    skills = {1:30100, 2:30100, 3:34300, 4:35100, 5:33100, 6:36100}

    def connect(index):
        sock, _, uid = login(game.port, identity.issue_ticket(*profiles[index]))
        join(sock)
        return sock, uid

    def seed(uid, items):
        with game.lock, db.conn:
            for template, count in items.items():
                db.set_item_detail(uid, make_item(template, count, uid).SerializeToString(), template)

    def funds(uid, item):
        with game.lock:
            return balance(uid,item).main_item.base_item.num

    def learn(sock, kind, skill, packet=99, fast=False):
        return call(sock, "LifeSkillLevelUp", packet, life_type=kind, skill_id=skill, is_force_study=fast)

    def listing(sock, kind):
        rsp = call(sock, "GetLifeSkill", 7, life_type=kind)
        assert rsp.status == ok and rsp.life_type == kind
        return {s.skill_id:(s.lv,s.start_study_time) for s in rsp.skill_info}

    def quota(sock, owner):
        assert call(sock, "ChangeSceneChannel", 8, scene_id=9999, channel_label=owner).status == ok
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data.scene_garden_data.left_like_num

    game.start()
    try:
        with patch("private_life_skills.clock", side_effect=lambda: now[0]):
            a, owner = connect(0)
            b, other = connect(1)
            with a, b:
                assert learn(a, 0, 34300).status == pb.StatusCode_BAD_REQ
                assert learn(a, 3, 36100).status == pb.StatusCode_PlayerLifeSkillNotExist
                assert learn(a, 3, 34300, fast=True).status == pb.StatusCode_BAD_REQ
                seed(owner, {101:999})
                assert learn(a, 3, 34300).status == pb.StatusCode_ITEM_NOT_ENOUGH
                assert not listing(a, 3) and funds(owner, 101) == 999
                seed(owner, {101:6000})
                for kind, skill in skills.items():
                    started = learn(a, kind, skill)
                    assert started.status == ok and started.life_skills.life_type == kind
                    assert learn(a, kind, skill) == started
                    assert listing(a, kind) == {skill:(0, epoch)}
                    assert not listing(b, kind)
                assert funds(owner, 101) == 0
                assert learn(a, 3, 34300, packet=100).status == pb.StatusCode_IsLearning
                assert learn(a, 3, 34100, packet=101).status == pb.StatusCode_CantLearningTwoSkill
                assert quota(a, other) == 10
                now[0] += 18
                # 1,782 remaining seconds = 148.5 rounded to 149 star stones.
                seed(owner, {102:148})
                assert learn(a, 3, 34300, fast=True).status == pb.StatusCode_ITEM_NOT_ENOUGH
                assert funds(owner, 102) == 148
                seed(owner, {102:149})
                accelerated = learn(a, 3, 34300, fast=True)
                assert accelerated.status == ok and accelerated.is_force_study
                assert learn(a, 3, 34300, fast=True) == accelerated
                assert listing(a,3) == {34300:(1,0)} and funds(owner,102) == 0
                assert quota(a,other) == 12
                assert learn(a,3,34300,packet=102).status == pb.StatusCode_LifeLvNotEnough
                with game.lock, db.conn:
                    life = base(owner, 3)
                    life.level = 2
                    db.set_life(owner, 3, life.SerializeToString())
                seed(owner,{101:2000,5000010:2})
                assert learn(a,3,34300,packet=102).status == pb.StatusCode_ITEM_NOT_ENOUGH
                assert funds(owner,101) == 2000 and funds(owner,5000010) == 2
                seed(owner,{5000010:3})
                assert learn(a,3,34300,packet=102).status == ok
                assert funds(owner,101) == funds(owner,5000010) == 0
                assert quota(a,other)==12
                # Existing community saves stored granted skills immediately with a historical timestamp.
                with game.lock, db.conn:
                    db.conn.execute("INSERT INTO player_kv VALUES(?,'life_skill','3',?)",
                                    (other,pickle.dumps({34300:(10,epoch),100:(512,0)})))
            game.stop()
            game.start()
            a, again = connect(0)
            b, second = connect(1)
            with a, b:
                assert (again,second) == (owner,other)
                assert listing(b,3) == {34300:(10,0)}
                assert learn(b,3,34300).status == pb.StatusCode_LifeSkillMaxLv
                assert quota(b,owner)==27
                now[0]=epoch+1799
                assert listing(a,1)=={30100:(0,epoch)}
                now[0]+=1
                assert call(a,"PlayerPing",119,client_time_ms=123).status==ok
                notice=next_msg(a,MsgId.LifeSkillLevelUpNotice,pb.LifeSkillLevelUpNotice)
                assert {row.life_type:[(s.skill_id,s.lv,s.start_study_time) for s in row.life_skill_info]
                        for row in notice.life_skills} == {k:[(v,1,0)] for k,v in skills.items() if k!=3}
                entry=call(a,"GetLifeInfo",130,life_type=3).life_skill
                assert [(r.skill_id,r.lv,r.start_study_time) for r in entry.life_skill_info]==[(34300,1,epoch+18)]
                now[0]=epoch+18+3600
                assert listing(a,3)=={34300:(2,0)}
                assert quota(a,other)==13 and quota(b,owner)==27
                assert call(a,"DoLikes",180).left_like_nums==12
                assert learn(a,3,34300,packet=135,fast=True).status==pb.StatusCode_SkillAlreadyStudy
    finally:
        game.stop()
    print("PASS native six life-skill timers, same-type exclusivity, configured costs, speed-up rounding, due notice, legacy saves, restart and garden quota.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
