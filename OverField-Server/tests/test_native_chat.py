"""Private realm chat through normal native TCP, including history, partition changes and offline mailboxes."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService,MsgId,pb
from local_game_wire import encode,receive
from local_identity import LocalIdentity
from test_game_realm import join,login
from test_observed_initialization import call

NEAR,WORLD,DM=pb.ChatChannel_Default,pb.ChatChannel_ChatRoom,pb.ChatChannel_Private

def barrier(sock):
    sock.sendall(encode(MsgId.PlayerPingReq,pb.PlayerPingReq(client_time_ms=369).SerializeToString(),119))
    notices=[]
    for _ in range(200):
        head,body=receive(sock)
        if head.msg_id==MsgId.PlayerPingRsp and head.packet_id==119:
            return notices
        if head.msg_id in (MsgId.ChatMsgNotice,MsgId.ChatMsgRecordInitNotice,MsgId.PrivateChatOfflineNotice,MsgId.ChangeChatChannelRsp):
            notices.append((head.msg_id,body))
    raise AssertionError("native chat/ping barrier")

def texts(notices):
    return [(m.type,m.msg.player_id,m.msg.text) for mid,buf in notices if mid==MsgId.ChatMsgNotice for m in [pb.ChatMsgNotice.FromString(buf)]]

def history(notices,kind):
    return [m.text for mid,buf in notices if mid==MsgId.ChatMsgRecordInitNotice for notice in [pb.ChatMsgRecordInitNotice.FromString(buf)]
            if notice.type==kind for m in notice.msg]

def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    people=[ident.create_profile(name) for name in ("聊天甲","聊天乙","聊天丙")]
    host=LocalGameService(ident,root,resources,port=0)
    clock=[1_800_000_000_000]
    def connect(index):
        ticket=ident.issue_ticket(*people[index])
        sock,_,pid=login(host.port,ticket)
        join(sock)
        return sock,pid,barrier(sock),ticket
    def send(sock,kind,message="",target=0,expression=0):
        clock[0]+=4000
        return call(sock,"SendChatMsg",type=kind,player_id=target,text=message,expression=expression)
    def switch(sock,channel):
        return call(sock,"ChangeChatChannel",channel_id=channel)

    host.start()
    try:
        with patch("private_chat.now",side_effect=lambda:clock[0]):
            a,pa,_,ticket=connect(0)
            b,pb_id,_,_=connect(1)
            c,pc,_,_=connect(2)
            with a,b,c:
                assert switch(a,0).status==pb.StatusCode_CHAT_CHANNEL_NOT_EXIST
                assert switch(a,10000).status==pb.StatusCode_CHAT_CHANNEL_NOT_EXIST
                assert switch(c,9999).channel_id==9999
                assert send(a,NEAR,"你\u200b好\ufeff").text=="你好"
                assert texts(barrier(b))==[(NEAR,pa,"你好")] and texts(barrier(c))==[(NEAR,pa,"你好")]
                assert send(a,NEAR,"同场频道").status==pb.StatusCode_OK
                assert texts(barrier(b))==[(NEAR,pa,"同场频道")] and texts(barrier(c))==[(NEAR,pa,"同场频道")]
                assert not texts(barrier(a)) # sender's receipt already adds its message to the retail UI.
                assert send(a,WORLD,"世界一号").status==pb.StatusCode_OK
                assert texts(barrier(b))==[(WORLD,pa,"世界一号")]
                assert not texts(barrier(c))
                assert call(b,"ChangeSceneChannel",scene_id=100,channel_label=1).status==pb.StatusCode_OK
                barrier(b)
                assert send(a,NEAR,"同频道跨场景").status==pb.StatusCode_OK
                assert texts(barrier(b))==[(NEAR,pa,"同频道跨场景")] and texts(barrier(c))==[(NEAR,pa,"同频道跨场景")]
                # The same scene on another actual channel must not receive or initialize channel 1's transcript.
                assert call(b,"ChangeSceneChannel",scene_id=9999,channel_label=2).status==pb.StatusCode_OK
                assert not history(barrier(b),NEAR)
                assert send(a,NEAR,"独立频道").status==pb.StatusCode_OK
                assert not texts(barrier(b)) and texts(barrier(c))==[(NEAR,pa,"独立频道")]
                # A player's own UID is also its separate home channel (never the scene ID).
                assert call(b,"ChangeSceneChannel",scene_id=9999,channel_label=pb_id).status==pb.StatusCode_OK
                assert not history(barrier(b),NEAR)
                assert send(b,NEAR,"仅在我家").status==pb.StatusCode_OK
                assert not texts(barrier(a)) and not texts(barrier(c))
                assert send(b,WORLD,"跨地图世界消息").status==pb.StatusCode_OK
                assert texts(barrier(a))==[(WORLD,pb_id,"跨地图世界消息")]
                assert not texts(barrier(c))
                assert switch(c,1).status==pb.StatusCode_OK
                assert history(barrier(c),WORLD)==["世界一号","跨地图世界消息"]
                assert switch(c,9999).status==pb.StatusCode_OK
                assert not history(barrier(c),WORLD)
                assert send(a,DM,"只告诉乙",pb_id).status==pb.StatusCode_OK
                assert texts(barrier(b))==[(DM,pa,"只告诉乙")]
                assert not texts(barrier(c))
                for sock,peer in ((a,pb_id),(b,pa)):
                    msg=call(sock,"PrivateChatMsgRecord",target_player_id=peer).msg_record
                    assert [(m.player_id,m.text) for m in msg]==[(pa,"只告诉乙")]
                assert not call(c,"PrivateChatMsgRecord",target_player_id=pa).msg_record
                assert not call(c,"PrivateChatMsgRecord",target_player_id=pb_id).msg_record
                assert send(a,DM,"<size=999>字</size>",pb_id).text=="＜size=999＞字＜/size＞"
                assert [m[2] for m in texts(barrier(b))]==["＜size=999＞字＜/size＞"]
                assert not texts(barrier(c))
                for bad in ("","a"*181,"换\u0001行","authorization: secret","测试 "+ticket):
                    assert send(a,WORLD,bad).status!=pb.StatusCode_OK
                assert send(a,WORLD,expression=0xffffffff).status!=pb.StatusCode_OK
                assert not texts(barrier(b))
                assert send(a,NEAR,expression=101).status==pb.StatusCode_OK
                assert texts(barrier(c))==[(NEAR,pa,"")]
                assert send(a,pb.ChatChannel_System,"系统广播").status!=pb.StatusCode_OK
                assert send(a,DM,"本人",pa).status!=pb.StatusCode_OK
                assert send(a,DM,"不存在",0xffffffff).status!=pb.StatusCode_OK
                assert call(b,"FriendBlack",player_id=pa).status==pb.StatusCode_OK
                assert send(a,DM,"被屏蔽",pb_id).status==pb.StatusCode_FRIEND_BLACK
                assert not texts(barrier(b))
                assert call(b,"FriendBlack",player_id=pa,is_remove=True).status==pb.StatusCode_OK
                b.close()
                assert send(a,DM,"离线时的新消息",pb_id).status==pb.StatusCode_OK
                assert not texts(barrier(c))
                assert switch(a,2).status==pb.StatusCode_OK
                assert send(a,WORLD,"二号世界").status==pb.StatusCode_OK
            host.stop()
            host.start()
            a,_,ar,_=connect(0)
            b,_,br,_=connect(1)
            c,_,cr,_=connect(2)
            with a,b,c:
                assert history(ar,WORLD)==["二号世界"] and history(br,WORLD)==["世界一号","跨地图世界消息"]
                assert not history(cr,WORLD)
                assert history(br,NEAR)==["仅在我家"]
                assert "同频道跨场景" in history(ar,NEAR)
                offline=[o for mid,buf in br if mid==MsgId.PrivateChatOfflineNotice for o in pb.PrivateChatOfflineNotice.FromString(buf).offline_msg]
                assert [(o.player_id,o.is_new_msg) for o in offline]==[(pa,True)]
                record=call(b,"PrivateChatMsgRecord",target_player_id=pa)
                assert record.status==pb.StatusCode_OK and record.msg_record[0].text=="离线时的新消息" and len(record.msg_record)==3
                assert not any(mid==MsgId.PrivateChatOfflineNotice for mid,_ in cr)
                assert call(a,"PrivateChatMsgRecord",target_player_id=pb_id).msg_record[0].text=="离线时的新消息"
                assert c is not None and pc!=pa and pc!=pb_id
            b,_,read,_=connect(1)
            with b:
                assert all(not x.is_new_msg for mid,buf in read if mid==MsgId.PrivateChatOfflineNotice for x in pb.PrivateChatOfflineNotice.FromString(buf).offline_msg)
            host.stop()
            # Use the persisted operator setting, including recovery of a saved world outside the new range.
            import json
            with ident._write() as db:
                db.execute("INSERT OR REPLACE INTO realm_rules VALUES(1,?)",(json.dumps({"world_min":14,"world_max":16}),))
            host=LocalGameService(ident,root,resources,port=0)
            host.start()
            a,_,notice,_=connect(0)
            with a:
                selected=[pb.ChangeChatChannelRsp.FromString(raw).channel_id for mid,raw in notice if mid==MsgId.ChangeChatChannelRsp]
                assert selected==[14]
                assert switch(a,13).status==pb.StatusCode_CHAT_CHANNEL_NOT_EXIST
                assert switch(a,16).status==pb.StatusCode_OK
                assert switch(a,17).status==pb.StatusCode_CHAT_CHANNEL_NOT_EXIST
    finally:
        host.stop()
    print("PASS native local/world/private chat: maps, world partitions 1..9999, DM pair isolation, offline unread/receipts, persistent history/selection, safe rendering and credentials.")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as t:
        run(Path(t),ap.parse_args().resources)
