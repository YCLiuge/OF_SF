"""Still-closed retail mini-games receive the native module-closed reply."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from local_game_service import LocalGameService, MsgId, pb
from local_game_wire import encode, receive
from local_identity import LocalIdentity
from test_game_realm import login, join
from test_native_gacha import call
from utils import db


# Values are independently checked against the PC NetworkEventIDNew constants.
EXAMPLES = (
    (4700,4701,"CollapseFail",{}),
)


def run(root,resources):
    identity=LocalIdentity(root/"identity.sqlite3")
    slot=identity.create_profile("棋盘兼容测试")
    game=LocalGameService(identity,root,resources,port=0)
    game.start()
    try:
        assert not game.realm_rules.enable_collapse_rooms and pb.StatusCode_MODULE_CLOSE==30
        sock,_,pid=login(game.port,identity.issue_ticket(*slot))
        with sock:
            join(sock)
            with game.lock:
                before=sorted(db.get_item_detail(pid))
                quests=sorted(db.get_quest(pid))
            for request_id,reply_id,name,fields in EXAMPLES:
                assert getattr(MsgId,name+"Req")==request_id and getattr(MsgId,name+"Rsp")==reply_id
                request=getattr(pb,name+"Req")(**fields)
                reply_type=getattr(pb,name+"Rsp")
                status=reply_type.DESCRIPTOR.fields_by_number[1]
                assert status.name=="status" and status.enum_type.name=="StatusCode"
                seq=request_id+100
                sock.sendall(encode(request_id,request.SerializeToString(),seq))
                for _ in range(256):
                    header,body=receive(sock)
                    if header.packet_id==seq and header.msg_id==reply_id: break
                else:
                    raise AssertionError(f"No {name} response on the live socket")
                reply=reply_type.FromString(body)
                assert [(f.name,v) for f,v in reply.ListFields()]==[("status",pb.StatusCode_MODULE_CLOSE)]
                pulse=call(sock,"PlayerPing",seq+1,client_time_ms=request_id)
                assert pulse.status==pb.StatusCode_OK and pulse.client_time_ms==request_id
            with game.lock:
                assert before==sorted(db.get_item_detail(pid)) and quests==sorted(db.get_quest(pid))
            assert call(sock,"ChangeSceneChannel",99,scene_id=9999).status==pb.StatusCode_OK
    finally:
        game.stop()
    print("PASS closed retail mini-games returned native MODULE_CLOSE, followed by live pings and a scene change; no inventory or quest mutation.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as state:
        run(Path(state),parser.parse_args().resources)
