"""Update the realm's name reservation and live character from the native name panel."""
import re
from datetime import date
import sqlite3

from local_identity import _name
from player_name_index import blocks_rename
from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb
from utils import db


@packet_handler(MsgId.ChangeNickNameReq)
class Handler(PacketHandler):
    def handle(self,session,data,packet_id):
        req=pb.ChangeNickNameReq.FromString(data)
        rsp=pb.ChangeNickNameRsp()
        try:
            name,key=_name(req.nick_name)
            birthday=""
            if req.birthday:
                match=re.fullmatch(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})",req.birthday)
                if not match:
                    raise ValueError("invalid birthday")
                birthday=date(*map(int,match.groups())).isoformat()
            # The identity index reserves names for played and as-yet unplayed accounts.
            occupied=db.conn.execute("""SELECT 1 FROM authority.profiles WHERE name_key=? AND slot!=?
                UNION ALL SELECT 1 FROM authority.realm_import_name_reservations WHERE name_key=? AND slot!=? LIMIT 1""",
                (key,session.slot,key,session.slot)).fetchone()
            if occupied or blocks_rename(db.conn,session.player_id,name):
                rsp.status=pb.StatusCode_DUPLICATED_NAME
            else:
                db.conn.execute("UPDATE authority.profiles SET nickname=?,name_key=? WHERE slot=?",(name,key,session.slot))
                db.conn.execute("INSERT OR IGNORE INTO authority.realm_named_profiles VALUES(?)",(session.slot,))
                db.conn.execute("UPDATE players SET player_name=?,birthday=CASE WHEN COALESCE(birthday,'')='' AND ?!='' THEN ? ELSE birthday END WHERE player_id=?",
                                (name,birthday,birthday,session.player_id))
                db.conn.execute("DELETE FROM authority.realm_pending_names WHERE slot=?",(session.slot,))
                session.needs_name=False
                rsp.nick_name=session.player_name=session.scene_player.player_name=name
                rsp.status=pb.StatusCode_OK
        except (ValueError,UnicodeError):
            rsp.status=pb.StatusCode_BAD_REQ
        except sqlite3.IntegrityError:
            rsp.status=pb.StatusCode_DUPLICATED_NAME
        session.send(MsgId.ChangeNickNameRsp,rsp,packet_id)

