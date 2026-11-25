from private_field_activities import riddle
from private_region_devices import changed
from proto import net_pb2 as pb
from private_request import handler


def update(session, req, rsp):
    status = riddle(session, req, rsp)
    if status == pb.StatusCode_OK and req.battle_state == pb.Finish and req.is_win:
        changed(session, 19, req.riddle_id)
    return status


Handler = handler("RiddleStateUpdate", update, replay=True)
