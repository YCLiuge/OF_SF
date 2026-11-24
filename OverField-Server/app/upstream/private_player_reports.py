"""Record a native report for this realm's operator using the server-side public player IDs."""
import time
from proto import net_pb2 as pb
from utils import db
import realm_uids


def submit(session,req,rsp):
    if req.report_status not in pb.ReportStatus.values() or req.chat_channel not in pb.ChatChannelType.values():
        return pb.StatusCode_BAD_REQ
    if any("\x00" in t for t in (req.reason,req.chat_content)) or len(req.reason)>2048 or len(req.chat_content)>4096:
        return pb.StatusCode_BAD_REQ
    target=req.reported_player_id
    photo=0
    if req.report_status==pb.PhotoShare:
        row=db.conn.execute("SELECT owner FROM realm_photos WHERE code=?", (req.code,)).fetchone()
        if row is None or target and target!=row[0]:
            return pb.StatusCode_BAD_REQ
        target, photo=row[0], req.code
    if not target or target==session.player_id or not db.conn.execute("SELECT 1 FROM players WHERE player_id=?", (target,)).fetchone():
        return pb.StatusCode_BAD_REQ
    content=req.chat_content if req.report_status==pb.ChatViolation else ""
    db.conn.execute("""INSERT INTO realm_reports(reporter,target,category,channel,content,reason,photo,created)
                    VALUES(?,?,?,?,?,?,?,?)""",(realm_uids.public_uid(session.player_id),realm_uids.public_uid(target),
                    req.report_status,req.chat_channel,content,req.reason,photo,int(time.time())))
    return pb.StatusCode_OK
