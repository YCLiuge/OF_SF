"""Original visitor, owner and neighbour sockets exercise shared daily limits and private home logs."""
import argparse
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_shop import CN
from test_game_realm import login, join, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_Quest


def run(root, resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    profiles=[identity.create_profile(n) for n in ("主人","访客","邻居")]
    game=LocalGameService(identity,root,resources,port=0)
    def connect(index):
        sock,_,player=login(game.port,identity.issue_ticket(*profiles[index]))
        _,notice=join(sock)
        return sock,player,notice.data.scene_garden_data
    def visit(sock,uid):
        assert call(sock,"ChangeSceneChannel",3,scene_id=9999,channel_label=uid).status==pb.StatusCode_OK
        return next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice).data.scene_garden_data
    def give(sock,ident):
        return call(sock,"DoLikes",ident)
    def history(sock):
        return call(sock,"GardenLikeRecord",23)
    ok=pb.StatusCode_OK
    start=datetime(2026,10,12,4,59,59,tzinfo=CN)
    day=datetime(2026,10,12,5,0,0,tzinfo=CN)
    game.start()
    try:
        with patch("private_shop.now",return_value=start):
            owner,pid,_=connect(0)
            visitor,vid,_=connect(1)
            neighbour,nid,_=connect(2)
            with owner,visitor,neighbour:
                assert give(owner,89).status==history(owner).status==pb.StatusCode_BAD_REQ
                assert visit(owner,pid).left_like_num==0 and give(owner,90).status==pb.StatusCode_BAD_REQ
                assert visit(visitor,pid).left_like_num==10
                with game.lock, db.conn:
                    db.conn.execute("UPDATE players SET sign=?,hide_value=0 WHERE player_id=?",("花园见",vid))
                    import private_bless_tasks as prayer
                    q=make_Quest(vid,prayer.UNLOCK)
                    q.status=pb.QuestStatus_Finish
                    db.set_quest(vid,q.quest_id,q.SerializeToString())
                    with patch.object(prayer,"choose",return_value=[522003,521005,521006,521007]):
                        prayer.ensure(game.active[profiles[1][0]])
                assert not history(owner).record
                first=give(visitor,30)
                assert first.status==ok and (first.like_nums,first.left_like_nums)==(1,9) and give(visitor,30)==first
                with game.lock:
                    raw=db.get_quest(vid,522003)
                    quest=pb.Quest.FromString(raw)
                    assert quest.status==quest.conditions[0].status==pb.QuestStatus_Completed
                for i in range(1,9):
                    response=give(visitor,30+i)
                    assert response.status==ok and (response.like_nums,response.left_like_nums)==(i+1,9-i)
                rows=history(owner).record
                assert len(rows)==1 and rows[0].player_id==vid and rows[0].sign=="花园见" and rows[0].like_num==9
                with game.lock, db.conn:
                    db.conn.execute("UPDATE players SET hide_value=? WHERE player_id=?", (pb.HideType_SIGN_Type,vid))
                assert history(owner).record[0].sign==history(visitor).record[0].sign==""
                assert call(owner,"GetGardenInfo",21).garden_info.likes_num==9
                assert visit(visitor,nid).left_like_num==1 and give(visitor,39).left_like_nums==0
                assert give(visitor,40).status==pb.StatusCode_TIPS_LIKES_NUM_LIMIT
                assert visit(visitor,pid).left_like_num==0
                assert give(visitor,41).status==pb.StatusCode_TIPS_LIKES_NUM_LIMIT
                visit(neighbour,pid)
                assert give(neighbour,51).status==ok
                assert [(r.player_id,r.like_num) for r in history(owner).record]==[(vid,9),(nid,1)]
                assert call(owner,"SwitchGardenStatus",22,is_open=False).status==ok
                assert history(visitor).status==give(neighbour,52).status==pb.StatusCode_BAD_REQ
                assert len(history(owner).record)==2
                assert call(owner,"SwitchGardenStatus",22,is_open=True).status==ok
            game.stop()
            game.start()
            visitor,again,view=connect(1)
            with visitor:
                assert again==vid and view.left_like_num==0 and view.likes_num==10
                assert give(visitor,70).status==pb.StatusCode_TIPS_LIKES_NUM_LIMIT
                with patch("private_shop.now",return_value=day):
                    assert visit(visitor,pid).left_like_num==10
                    actual=give(visitor,70)
                    assert actual.status==ok and actual.left_like_nums==9 and actual.like_nums==11
                    assert history(visitor).record[0].like_num==10
    finally:
        game.stop()
    print("PASS native daily garden likes across homes, quota rollover, request replay, history/privacy, active quest, real totals and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
