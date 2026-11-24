"""Native room decor applies to configured anchors and is visible to visiting players."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from test_game_realm import join, login, next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root, resources):
    identity = LocalIdentity(root / "identity.sqlite3")
    profiles = [identity.create_profile(name) for name in ("房主", "访客", "外人")]
    host = LocalGameService(identity, root, resources, port=0)
    lamp, wall = 5050998, 5050999

    def connect(index):
        sock, _, pid = login(host.port, identity.issue_ticket(*profiles[index]))
        join(sock)
        return sock, pid

    def visit(sock, channel, seq):
        assert call(sock, "ChangeSceneChannel", seq, channel_label=channel, scene_id=9999).status == pb.StatusCode_OK
        return next_msg(sock, MsgId.SceneDataNotice, pb.SceneDataNotice).data

    def decor(pos, template):
        return pb.RoomDecorData(pos_id=pos, decor_id=template, item_id=template)

    host.start()
    try:
        a, owner = connect(0)
        b, friend = connect(1)
        c, stranger = connect(2)
        with a, b, c:
            visit(a, owner, 12)
            visit(b, owner, 12)
            visit(c, stranger, 12)
            with host.lock, db.conn:
                for key, count in ((lamp,1),(wall,0)):
                    db.set_item_detail(owner, make_item(key,count,owner).SerializeToString(),key)
            changes = [decor(1, lamp), decor(5, wall)]
            assert call(a,"SetRoomDecor",13,decor_list=changes).status == pb.StatusCode_ITEM_NOT_ENOUGH
            with host.lock, db.conn:
                assert not db.conn.execute("SELECT 1 FROM player_kv WHERE ns='room_decor'").fetchone()
                db.set_item_detail(owner,make_item(wall,1,owner).SerializeToString(),wall)
            assert call(a,"SetRoomDecor",14,decor_list=[decor(5,lamp)]).status != pb.StatusCode_OK
            assert call(a,"SetRoomDecor",15,decor_list=[decor(1,lamp),decor(1,0)]).status != pb.StatusCode_OK
            assert call(b,"SetRoomDecor",16,decor_list=changes).status != pb.StatusCode_OK
            assert call(a,"SetRoomDecor",17,decor_list=changes).status == pb.StatusCode_OK
            assert list(next_msg(b,MsgId.RoomDecorNotice,pb.RoomDecorNotice).decor_list) == changes
            assert call(a,"SetRoomDecor",17,decor_list=changes).status == pb.StatusCode_OK
            clear = [decor(1,0)]
            assert call(a,"SetRoomDecor",18,decor_list=clear).status == pb.StatusCode_OK
            assert list(next_msg(b,MsgId.RoomDecorNotice,pb.RoomDecorNotice).decor_list) == clear
            assert not visit(c, 1, 19).room_decor_list
            assert list(visit(c, owner, 20).room_decor_list) == [changes[1]]
            with host.lock, db.conn:
                for who, status in ((friend,pb.FriendStatus_FRIEND),(stranger,pb.FriendStatus_APPLY)):
                    db.init_friend(owner,who)
                    db.set_friend_info(owner,who,"friend_status",pb.FriendStatus_FRIEND)
                    db.set_friend_info(who,owner,"friend_status",status)
                for who, scene, key, duration, phase in ((owner,9999,31,19,pb.Finish),(owner,1,66,4,pb.Finish),
                        (friend,9999,31,20,pb.Finish),(friend,9999,32,17,pb.Start),(stranger,9999,31,12,pb.Finish)):
                    data = pb.ChallengeData(challenge_id=key,use_time=duration,state=phase)
                    db.set_challenge(who,scene,key,data.SerializeToString())
            rank = call(a,"ChallengeFriendRank",21)
            assert rank.status == pb.StatusCode_OK and rank.self_challenge.player_id == owner
            assert [(r.challenge_id,r.use_time) for r in rank.self_challenge.challenge_infos] == [(31,19)]
            assert [r.challenge.player_id for r in rank.rank_info] == [friend]
            assert [(r.challenge_id,r.use_time) for r in rank.rank_info[0].challenge.challenge_infos] == [(31,20)]
        host.stop()
        host.start()
        b, same = connect(1)
        with b:
            assert same == friend and list(visit(b, owner, 22).room_decor_list) == [decor(5,wall)]
    finally:
        host.stop()
    print("PASS native room decor, configured anchors, atomic batch, visitor scope, replay and restart; mutual friend challenge board, scene and completion filters.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    with TemporaryDirectory() as temp:
        run(Path(temp), parser.parse_args().resources)
