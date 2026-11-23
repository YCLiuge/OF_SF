"""Restore canonical private gameplay identifiers after removing free-form client fields."""
from collections import deque
from threading import Lock

from proto import net_pb2 as pb
from social_query import search_key


class SocialGameFields:
    def __init__(self):
        self.rifts=deque(maxlen=512)
        self.lock=Lock()

    def observe(self,msg):
        if isinstance(msg,pb.SelfIntervalInitRsp):
            rows=(msg.interval,) if msg.is_start and msg.HasField("interval") else ()
        elif isinstance(msg,pb.FriendIntervalInitRsp):
            rows=(*msg.friend_infos,*msg.join_infos)
        else:
            return
        if msg.status!=pb.StatusCode_OK:
            return
        with self.lock:
            for row in rows:
                key=(row.player_id,str(row.create_time))
                if row.player_id and row.create_time>0 and key not in self.rifts:
                    self.rifts.append(key)

    def outgoing(self,msg,clean):
        if isinstance(msg,pb.SendChatMsgReq):
            from private_chat_text import chat_text
            text=chat_text(msg.text)
            clean(msg)
            if text is not None:
                msg.text=text
            else:
                msg.expression=0
        elif isinstance(msg,pb.FriendSearchReq):
            value=search_key(msg.search_args)
            clean(msg)
            msg.search_args=value
        elif isinstance(msg,pb.ChangeNickNameReq):
            from datetime import date
            from local_identity import _name
            try:
                name,_=_name(msg.nick_name)
                birthday=msg.birthday if msg.birthday and date.fromisoformat(msg.birthday).isoformat()==msg.birthday else ""
            except ValueError:
                name,birthday="",""
            clean(msg)
            msg.nick_name,msg.birthday=name,birthday
        elif isinstance(msg,(pb.IntervalJoinReq,pb.IntervalRewardReq)):
            key=(msg.friend_id,msg.interval_param)
            clean(msg)
            with self.lock:
                if key in self.rifts:
                    msg.interval_param=key[1]
        else:
            clean(msg)
