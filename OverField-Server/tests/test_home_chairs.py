"""Native chair occupancy during home scene entry, conflicts and reconnection."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import login, join_after_intro


def run(root,resources):
    ids=LocalIdentity(root/"identity.sqlite3")
    one,two=ids.create_profile("座位一"),ids.create_profile("座位二")
    game=LocalGameService(ids,root,resources,port=0)
    def open_world(profile):
        c,_,pid=login(game.port,ids.issue_ticket(*profile))
        _,scene=join_after_intro(c)
        seats=[(s.chair_id,s.seat_id,s.player_id) for s in scene.data.chair_info_list]
        assert scene.data.scene_id==9999
        return c,pid,seats
    def sit(c,seat,slot,take):
        req=pb.SceneSitChairReq(chair_id=seat,seat_id=slot,is_sit=take)
        c.sendall(encode(MsgId.SceneSitChairReq,req.SerializeToString(),33))
        for _ in range(150):
            h,b=receive(c)
            if h.msg_id==MsgId.SceneSitChairRsp and h.packet_id==33:
                return pb.SceneSitChairRsp.FromString(b).status
        raise AssertionError("No reply for chair request")
    game.start()
    try:
        c,pid,empty=open_world(one)
        assert not empty
        with c:
            assert sit(c,0,0,True)==pb.StatusCode_BAD_REQ
            assert sit(c,58,3,True)==pb.StatusCode_BAD_REQ
            assert sit(c,58,2,True)==pb.StatusCode_OK
            peer,_,occupied=open_world(two)
            with peer:
                assert occupied==[(58,2,pid)]
                assert sit(peer,58,2,True)==pb.StatusCode_ChairSeatTaken
                assert sit(c,58,2,False)==pb.StatusCode_OK
                assert sit(peer,58,2,True)==pb.StatusCode_OK
        # Reconnect to the private realm and verify disconnected occupants were cleared.
        q,_,empty=open_world(two)
        with q:
            assert not empty
            assert sit(q,58,2,True)==pb.StatusCode_OK
    finally:
        game.stop()
    print("PASS scene 9999 native chair IDs/seat limits, state only for occupants, conflicts and reconnection.")

if __name__=="__main__":
    args=argparse.ArgumentParser()
    args.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),args.parse_args().resources)
