"""Retail Gomoku robot awards and native two-player/observer chessboards over TCP."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_game_service import LocalGameService, MsgId, pb
from local_identity import LocalIdentity
from private_arcade_wallet import wallet
from private_gomoku import SIDE, TABLE, line
from private_mp_rooms import periods
from test_game_realm import join,login,next_msg
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item

OK=pb.StatusCode_OK


def test(root,resources):
    identities=LocalIdentity(root/"identity.sqlite3")
    profiles=[identities.create_profile("棋局"+name) for name in "甲乙丙丁"]
    host=LocalGameService(identities,root,resources,port=0)
    seq=10
    def ask(sock,name,**kw):
        nonlocal seq
        seq+=1
        return call(sock,name,seq,**kw)
    def connect(i):
        sock,_,pid=login(host.port,identities.issue_ticket(*profiles[i]))
        join(sock)
        return sock,pid
    def visit(sock,ch):
        assert ask(sock,"ChangeSceneChannel",scene_id=9999,channel_label=ch).status==OK
        next_msg(sock,MsgId.SceneDataNotice,pb.SceneDataNotice)
    fid=44010
    def action(sock,name,**kw):
        return ask(sock,"GomokuOnlinePlayer"+name,id=fid,**kw)
    def board(sock):
        return ask(sock,"GetGomokuOnlineChessboard",id=fid)
    def message(sock,name):
        return next_msg(sock,getattr(MsgId,name),getattr(pb,name))
    def seed(pid,item,n):
        db.set_item_detail(pid,make_item(item,n,pid).SerializeToString(),item_id=item)
    winning=[0]*(SIDE*SIDE)
    for c in range(5):
        winning[c]=1
    for c in (0,2,4,6):
        winning[SIDE+c]=2
    params=dict(board=winning,self=1,lastRow=0,lastCol=4,is_use_game_coin=True)
    # The national PC NetworkEventIDNew includes this contiguous range.
    names=("GomokuSinglePlayerRewardReq","GomokuSinglePlayerRewardRsp",
           "GomokuOnlinePlayerReadyReq","GomokuOnlinePlayerReadyRsp","GomokuOnlinePlayerWatchReq","GomokuOnlinePlayerWatchRsp",
           "GomokuOnlinePlayerNotice","GomokuOnlinePlayerWatchNotice","GomokuOnlinePlayerRemoveNotice",
           "GetGomokuOnlineChessboardReq","GetGomokuOnlineChessboardRsp",
           "GomokuOnlinePlayerPlaceStoneReq","GomokuOnlinePlayerPlaceStoneRsp","GomokuOnlinePlayerPlaceStoneNotice",
           "GomokuOnlinePlayerLeaveReq","GomokuOnlinePlayerLeaveRsp","GomokuOnlinePlayerLeaveNotice",
           "GomokuOnlinePlayerStartPlayReq","GomokuOnlinePlayerStartPlayRsp","GomokuOnlinePlayerStartPlayNotice","GomokuOnlineSettlementNotice")
    assert [getattr(MsgId,name) for name in names]==list(range(4600,4621))
    host.start()
    try:
        (a,aid),(b,bid),(c,cid),(d,did)=[connect(i) for i in range(4)]
        with a,b,c,d:
            assert ask(a,"GomokuSinglePlayerReward",**params).status!=OK
            for sock in (a,b,c):
                visit(sock,1)
            visit(d,2)
            with host.lock,db.conn:
                seed(aid,TABLE,1)
                seed(aid,117,5)
                seed(bid,117,1)
                initial=wallet(aid,121)
                initial_b=wallet(bid,121)
            assert ask(a,"PlaceFurniture",furniture_item_id=TABLE,furniture_id=fid,pos={"x":3}).status==OK
            assert board(d).status==pb.StatusCode_FURNITURE_NOT_EXIST
            assert len(board(c).board)==169
            invalid=dict(params,board=[0]*169)
            assert ask(a,"GomokuSinglePlayerReward",**invalid).status!=OK
            assert ask(a,"GomokuSinglePlayerReward",**dict(params,lastCol=6)).status!=OK
            reward=ask(a,"GomokuSinglePlayerReward",**params)
            assert reward.status==OK and reward.isSelfWin and reward.items[0].main_item.base_item.num==4500,reward
            assert ask(a,"GomokuSinglePlayerReward",**params)==reward
            lost=ask(b,"GomokuSinglePlayerReward",**dict(params,self=2,lastRow=1,lastCol=6))
            assert lost.status==OK and not lost.isSelfWin and lost.items[0].main_item.base_item.num==1200,lost
            with host.lock:
                assert wallet(aid,117)==2 and wallet(aid,121)==initial+4500
                assert wallet(bid,117)==0 and wallet(bid,121)==initial_b+1200
            # With week allowance exhausted, the board can be redeemed once allowance returns.
            week="w:"+periods()[1]
            with host.lock,db.conn:
                seed(cid,117,1)
                db.conn.execute("INSERT OR REPLACE INTO player_kv VALUES(?,'arcade_score',?,?)",(cid,week,5000000))
            cap=ask(c,"GomokuSinglePlayerReward",**params)
            assert cap.status==OK and not cap.items and wallet(cid,117)==1
            with host.lock,db.conn:
                db.conn.execute("UPDATE player_kv SET v=4999993 WHERE player_id=? AND ns='arcade_score' AND k=?",(cid,week))
            limited=ask(c,"GomokuSinglePlayerReward",**params)
            assert limited.status==OK and limited.items[0].main_item.base_item.num==7 and wallet(cid,117)==0
            assert action(a,"Ready",coin_num=1).status!=OK
            assert action(a,"Ready").status==OK
            assert action(a,"StartPlay").status!=OK
            assert action(b,"Ready").status==OK
            ready=message(a,"GomokuOnlinePlayerNotice")
            assert {ready.player1_id,ready.player2_id}=={aid,bid} and ready.first_player_id in (aid,bid)
            assert action(c,"Ready").status!=OK
            assert action(c,"Watch").status==OK
            assert action(c,"StartPlay").status!=OK
            assert [r.player_id for r in board(c).watch_players]==[cid]
            assert action(b,"StartPlay").turn_deadline>0
            start=message(c,"GomokuOnlinePlayerStartPlayNotice")
            plays={aid:a,bid:b}
            first=plays[start.first_player_id]
            other=next(sock for pid,sock in plays.items() if pid!=start.first_player_id)
            assert board(c).next_player_id==start.first_player_id
            assert action(other,"PlaceStone",row=0,col=0).status!=OK
            assert action(c,"PlaceStone",row=0,col=0).status!=OK
            assert action(first,"PlaceStone",row=13,col=0).status!=OK
            for i in range(5):
                result=action(first,"PlaceStone",row=i,col=i)
                assert result.status==OK and result.is_win==(i==4),result
                changed=message(c,"GomokuOnlinePlayerPlaceStoneNotice")
                assert (changed.row,changed.col,changed.chess,changed.is_win)==(i,i,1,i==4)
                if i!=4:
                    assert action(other,"PlaceStone",row=i,col=i).status!=OK
                    assert action(other,"PlaceStone",row=0,col=i+1).status==OK
                    message(c,"GomokuOnlinePlayerPlaceStoneNotice")
            over=message(c,"GomokuOnlinePlayerRemoveNotice")
            assert over.winner_id==start.first_player_id and over.over_reason==pb.EGomokuOverReason_Win
            snapshot=board(c)
            assert snapshot.boardState==pb.EGomokuChessBoardStatus_Over
            assert action(c,"Ready").status!=OK and board(c)==snapshot
            assert action(a,"Ready").status==OK and action(b,"Ready").status==OK
            start2=action(a,"StartPlay")
            assert start2.status==OK
            with patch("private_gomoku.milliseconds",return_value=start2.turn_deadline+1):
                timed=message(c,"GomokuOnlinePlayerRemoveNotice")
                assert timed.over_reason==pb.EGomokuOverReason_Timeout and timed.winner_id in (aid,bid)
            assert action(a,"Ready").status==OK and action(b,"Ready").status==OK
            assert action(a,"StartPlay").status==OK
            visit(b,2)
            left=message(c,"GomokuOnlinePlayerRemoveNotice")
            assert left.over_reason==pb.EGomokuOverReason_Leave and left.winner_id==aid
            visit(b,1)
            assert action(a,"Ready").status==OK and action(b,"Ready").status==OK
            assert action(a,"StartPlay").status==OK
            assert ask(a,"TakeOutFurniture",furniture_id=fid,is_put_in_package=True).status==OK
            removed=message(c,"GomokuOnlinePlayerRemoveNotice")
            assert removed.over_reason==pb.EGomokuOverReason_Furniture and not removed.winner_id
            assert board(c).status!=OK and board(d).status!=OK
            assert ask(a,"PlaceFurniture",furniture_item_id=TABLE,furniture_id=fid,pos={"x":3}).status==OK
            assert action(a,"Ready").status==OK and action(b,"Ready").status==OK
            assert action(a,"StartPlay").status==OK and action(c,"Watch").status==OK
            b.close()
            disconnected=message(c,"GomokuOnlinePlayerRemoveNotice")
            assert disconnected.over_reason==pb.EGomokuOverReason_Leave and disconnected.winner_id==aid
            assert action(c,"Leave").status==OK
        host.stop()
        host.start()
        a,same=connect(0)
        with a:
            visit(a,1)
            assert ask(a,"GomokuSinglePlayerReward",**params)==reward
            with host.lock:
                assert wallet(same,117)==2 and wallet(same,121)==initial+4500
        # Diagonal, horizontal and vertical wins, with no wrap at a board edge.
        for dy,dx in ((1,1),(-1,1),(1,0),(0,1)):
            cells=bytearray(169)
            for i in range(5):
                cells[(6+i*dy)*SIDE+(5+i*dx)]=2
            assert line(cells,6,5)
        cells=bytearray(169)
        cells[11:16]=bytes([1]*5)
        assert not line(cells,0,12)
    finally:
        host.stop()
    print("PASS retail 13x13 solo win/loss and 6002/6003 ticket rewards, durable retry, two seats, spectator, scoped events, timeout, departures and furniture removal.")


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--resources",type=Path,required=True)
    with TemporaryDirectory() as root:
        test(Path(root),p.parse_args().resources.resolve())
