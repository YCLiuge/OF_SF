"""Explicit failure for a well-formed, known request after realm authentication."""
from network.msg_id import MsgId
from proto import net_pb2 as pb


# These features have no local operator or gameplay use. Keep the previously
# observed failure code and do not register or process any future upstream handler.
OWNER_DISABLED = frozenset((
    MsgId.GetExpressAddressReq, MsgId.UploadExpressAddressReq,
    MsgId.ClientLogAuthReq, MsgId.ClientLogMessageReq,
))


# Their status replies exist in the retail client. These activities have no private session or settlement.
CLOSED_MINIGAMES = frozenset((MsgId.CollapseFailReq,))


def unsupported_status(msg_id):
    return pb.StatusCode_MODULE_CLOSE if msg_id in CLOSED_MINIGAMES else pb.StatusCode_FAIL


def status_replies():
    pairs = {}
    for name, number in vars(MsgId).items():
        if not name.endswith("Req") or name in ("VerifyLoginTokenReq", "PlayerLoginReq"):
            continue
        reply_name = name[:-3] + "Rsp"
        request = getattr(pb,name,None)
        reply = getattr(pb,reply_name,None)
        reply_number = getattr(MsgId,reply_name,None)
        if request is None or reply is None or reply_number is None:
            continue
        status = reply.DESCRIPTOR.fields_by_name.get("status")
        if status is not None and status.enum_type is not None and status.enum_type.name == "StatusCode":
            pairs[number] = (request, reply, reply_number)
    return pairs
