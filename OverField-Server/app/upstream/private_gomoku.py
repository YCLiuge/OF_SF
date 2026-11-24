"""National 13-by-13 furniture chessboard, native notices and robot settlement."""
import hashlib
import random
import time
from functools import partial

from network.msg_id import MsgId
from private_arcade_wallet import config, wallet
from private_gameplay_transaction import run
from private_mp_rooms import award_score
import realm_uids
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_PlayerBriefInfo

SIDE = 13
CELLS = SIDE * SIDE
TABLE = 5044010
HOME = 9999
EMPTY = bytes(CELLS)
BAD = pb.StatusCode_BAD_REQ
OK = pb.StatusCode_OK
NONE = pb.EGomokuChessBoardStatus_None
READY = pb.EGomokuChessBoardStatus_Start
PLAYING = pb.EGomokuChessBoardStatus_Playing
OVER = pb.EGomokuChessBoardStatus_Over
next_alarm = 0
rooms = {}


def initialize(conn,scene_members):
    global next_alarm, rooms
    rooms = scene_members
    next_alarm = 0
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS furniture_room_id ON furnitures(scene_id,channel_id,furniture_id);
        CREATE TEMP TABLE IF NOT EXISTS gomoku_boards(
            channel INTEGER NOT NULL, id INTEGER NOT NULL, state INTEGER NOT NULL, first INTEGER NOT NULL,
            turn INTEGER NOT NULL, deadline INTEGER NOT NULL, board BLOB NOT NULL, PRIMARY KEY(channel,id));
        CREATE INDEX IF NOT EXISTS gomoku_clock ON gomoku_boards(deadline) WHERE deadline>0;
        CREATE TEMP TABLE IF NOT EXISTS gomoku_seats(
            player INTEGER PRIMARY KEY, channel INTEGER NOT NULL,id INTEGER NOT NULL, seat INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS gomoku_seating ON gomoku_seats(channel,id,seat);
        CREATE TABLE IF NOT EXISTS realm_gomoku_solo(
            player INTEGER PRIMARY KEY, digest BLOB NOT NULL, receipt BLOB NOT NULL);
    """)


def milliseconds():
    return int(time.time() * 1000)


def line(board, row, col):
    stone = board[row*SIDE+col]
    if not stone:
        return False
    for dy, dx in ((1,0),(0,1),(1,1),(1,-1)):
        length = 1
        for sign in (-1,1):
            y,x = row+sign*dy,col+sign*dx
            while 0<=y<SIDE and 0<=x<SIDE and board[y*SIDE+x]==stone:
                length+=1
                y,x=y+sign*dy,x+sign*dx
        if length>=5:
            return True
    return False


def winning_cells(board):
    return {i for i,color in enumerate(board) if color and line(board,*divmod(i,SIDE))}


def solo_result(req):
    if len(req.board)!=CELLS or req.self not in (1,2) or not (0<=req.lastRow<SIDE and 0<=req.lastCol<SIDE):
        return None
    if any(stone not in (0,1,2) for stone in req.board):
        return None
    board = bytearray(req.board)
    first, second = board.count(1), board.count(2)
    last=req.lastRow*SIDE+req.lastCol
    if first-second not in (0,1) or board[last]!=req.self:
        return None
    wins=winning_cells(board)
    last_type=1 if first>second else 2
    if any(board[cell]!=last_type for cell in wins):
        return None
    if not wins:
        return False if 0 not in board else None
    # The packet carries the human's last position, even when the robot won.
    # The winning stone must erase every winning line when taken back.
    positions = [last] if req.self==last_type else wins
    for cell in positions:
        board[cell]=0
        ended_before=bool(winning_cells(board))
        board[cell]=last_type
        if not ended_before:
            return req.self==last_type
    return None


def settle_solo(s,req,rsp,pending):
    if s.scene_id!=HOME or s.dungeon[0]:
        return BAD
    won=solo_result(req)
    if won is None:
        return BAD
    rsp.isSelfWin=won
    if not req.is_use_game_coin:
        return OK
    # No separate start ID exists in the retail solo protocol. Preserve the
    # last paid result across reconnects so a retried terminal board pays once.
    digest=hashlib.sha256(bytes(req.board)+bytes([req.self,req.lastRow,req.lastCol])).digest()
    entry=db.conn.execute("SELECT digest,receipt FROM realm_gomoku_solo WHERE player=?",(s.player_id,)).fetchone()
    if entry and entry[0]==digest:
        rsp.ParseFromString(entry[1])
        return OK
    count=min(3,wallet(s.player_id,117))
    if not count:
        return pb.StatusCode_ITEM_NOT_ENOUGH
    prize=pb.MPPlayRoomSettlementNotice()
    # The retail result view previews 6002 (loss) or 6003 (win) times up to three tokens.
    pack=award_score(s.player_id,3 if won else 2,prize,units=count)
    if not pack:
        return OK
    pending.balances.change(117,-count)
    pending.pack.items.extend(pack.items)
    rsp.items.extend(prize.rewards)
    db.conn.execute("INSERT OR REPLACE INTO realm_gomoku_solo VALUES(?,?,?)",(s.player_id,digest,rsp.SerializeToString()))
    return OK


def table_at(s, fid):
    if s.scene_id!=HOME or s.dungeon[0] or fid<=0:
        return False
    row=db.conn.execute("SELECT furniture_detail_blob FROM furnitures WHERE scene_id=? AND channel_id=? AND furniture_id=?",
                        (HOME,s.channel_id,fid)).fetchone()
    return bool(row and pb.FurnitureDetailsInfo.FromString(row[0]).furniture_item_id==TABLE)


def board_at(key):
    return db.conn.execute("SELECT state,first,turn,deadline,board FROM gomoku_boards WHERE channel=? AND id=?",key).fetchone()


def save(key,state,first,turn,deadline,board):
    global next_alarm
    db.conn.execute("INSERT OR REPLACE INTO gomoku_boards VALUES(?,?,?,?,?,?,?)",(*key,state,first,turn,deadline,board))
    if deadline and (not next_alarm or deadline<next_alarm):
        next_alarm=deadline


def seats(key):
    return dict(db.conn.execute("SELECT player,seat FROM gomoku_seats WHERE channel=? AND id=? ORDER BY seat,player",key))


def notify(key, players, cmd, message):
    payload = None
    for peer in rooms.get((HOME,key[0]),()):
        if peer.running and peer.player_id in players:
            if payload is None:
                payload=realm_uids.outgoing(message).SerializeToString()
            peer.send(cmd,payload,0,is_bin=True)


def finish(key,players,board,winner,reason):
    save(key,OVER,board[1],0,0,board[4])
    for pid,seat in players.items():
        if seat:
            notice=pb.GomokuOnlineSettlementNotice(status=OK,id=key[1],is_win=pid==winner,over_reason=reason)
            notify(key,(pid,),MsgId.GomokuOnlineSettlementNotice,notice)
    notify(key,players,MsgId.GomokuOnlinePlayerRemoveNotice,
           pb.GomokuOnlinePlayerRemoveNotice(status=OK,id=key[1],winner_id=winner,over_reason=reason))


def expire():
    stamp=milliseconds()
    rows=db.conn.execute("SELECT channel,id FROM gomoku_boards WHERE deadline>0 AND deadline<=?",(stamp,)).fetchall()
    for key in rows:
        board=board_at(key)
        players=seats(key)
        winner=next((pid for pid,seat in players.items() if seat and pid!=board[2]),0)
        finish(key,players,board,winner,pb.EGomokuOverReason_Timeout)
    refresh_alarm()
    return bool(rows)


def refresh_alarm():
    global next_alarm
    next_alarm=db.conn.execute("SELECT min(deadline) FROM gomoku_boards WHERE deadline>0").fetchone()[0] or 0


def due():
    return next_alarm and milliseconds()>=next_alarm


def remove_table(channel,fid):
    key=(channel,fid)
    board=board_at(key)
    if board:
        if board[0]!=OVER:
            finish(key,seats(key),board,0,pb.EGomokuOverReason_Furniture)
        db.conn.execute("DELETE FROM gomoku_boards WHERE channel=? AND id=?",key)
        db.conn.execute("DELETE FROM gomoku_seats WHERE channel=? AND id=?",key)


def depart(player):
    row=db.conn.execute("SELECT channel,id,seat FROM gomoku_seats WHERE player=?",(player,)).fetchone()
    if not row:
        return
    key=row[:2]
    board=board_at(key)
    players=seats(key)
    notify(key,players,MsgId.GomokuOnlinePlayerLeaveNotice,
           pb.GomokuOnlinePlayerLeaveNotice(status=OK,id=key[1],player_id=player,is_player=bool(row[2])))
    db.conn.execute("DELETE FROM gomoku_seats WHERE player=?",(player,))
    if board and row[2]:
        if board[0]==PLAYING:
            winner=next((pid for pid,seat in players.items() if seat and pid!=player),0)
            finish(key,players,board,winner,pb.EGomokuOverReason_Leave)
        elif board[0]!=OVER:
            save(key,NONE,0,0,0,EMPTY)
    if len(players)==1:
        db.conn.execute("DELETE FROM gomoku_boards WHERE channel=? AND id=?",key)


def online(name,s,req,rsp):
    if not table_at(s,req.id):
        return pb.StatusCode_FURNITURE_NOT_EXIST
    if due():
        expire()
    key=(s.channel_id,req.id)
    pid=s.player_id
    players=seats(key)
    board=board_at(key) or (NONE,0,0,0,EMPTY)
    state,first,turn,deadline,cells=board
    if name=="get":
        rsp.boardState=state
        connected={peer.player_id for peer in rooms.get((HOME,key[0]),()) if peer.running}
        for person,seat in players.items():
            group=rsp.chest_players if seat else rsp.watch_players
            make_PlayerBriefInfo(person,group.add(),online=person in connected)
        rsp.next_player_id,rsp.turn_deadline=turn,deadline
        rsp.board.extend(cells)
        return OK
    if name=="leave":
        if pid not in players:
            return BAD
        depart(pid)
        return OK
    if name in ("ready","watch"):
        if name=="ready" and req.coin_num:
            # Retail RequestReady sends only the furniture ID, with zero tokens.
            return BAD
        other_board=db.conn.execute("SELECT channel,id,seat FROM gomoku_seats WHERE player=?",(pid,)).fetchone()
        if other_board and (other_board[:2]!=key or bool(other_board[2])!=(name=="ready")):
            return BAD
        if name=="ready" and state==OVER:
            db.conn.execute("DELETE FROM gomoku_seats WHERE channel=? AND id=? AND seat>0",key)
            players=seats(key)
            state,first,turn,deadline,cells=NONE,0,0,0,EMPTY
        if pid in players:
            return OK if bool(players[pid])==(name=="ready") else BAD
        if name=="watch":
            seat=0
        else:
            free=[number for number in (1,2) if number not in players.values()]
            if not free or state==PLAYING:
                return BAD
            seat=free[0]
        db.conn.execute("INSERT INTO gomoku_seats VALUES(?,?,?,?)",(pid,*key,seat))
        players[pid]=seat
        if seat:
            seated={position:who for who,position in players.items() if position}
            if len(seated)==2:
                state,first=READY,random.choice(list(seated.values()))
            notice=pb.GomokuOnlinePlayerNotice(status=OK,first_player_id=first,
                        player1_id=seated.get(1,0),player2_id=seated.get(2,0))
            notify(key,players,MsgId.GomokuOnlinePlayerNotice,notice)
        else:
            notify(key,players,MsgId.GomokuOnlinePlayerWatchNotice,pb.GomokuOnlinePlayerWatchNotice(status=OK,id=req.id,player_id=pid))
        save(key,state,first,turn,deadline,cells)
        return OK
    if not players.get(pid):
        return BAD
    if name=="start":
        if state==PLAYING:
            rsp.turn_deadline=deadline
            return OK
        if state!=READY:
            return BAD
        deadline=milliseconds()+config("Gomoku_OnLine_FailTime")[0]*1000
        save(key,PLAYING,first,first,deadline,EMPTY)
        rsp.turn_deadline=deadline
        notify(key,players,MsgId.GomokuOnlinePlayerStartPlayNotice,
               pb.GomokuOnlinePlayerStartPlayNotice(status=OK,id=req.id,first_player_id=first,turn_deadline=deadline))
        return OK
    if state!=PLAYING or pid!=turn or not (0<=req.row<SIDE and 0<=req.col<SIDE):
        return BAD
    index=req.row*SIDE+req.col
    if cells[index]:
        return BAD
    cells=bytearray(cells)
    stone=1 if pid==first else 2
    cells[index]=stone
    win=line(cells,req.row,req.col)
    ended=win or 0 not in cells
    turn=0 if ended else next(other for other,seat in players.items() if seat and other!=pid)
    deadline=0 if ended else milliseconds()+config("Gomoku_OnLine_FailTime")[0]*1000
    rsp.is_win,rsp.next_player_id,rsp.turn_deadline=win,turn,deadline
    notice=pb.GomokuOnlinePlayerPlaceStoneNotice(status=OK,id=req.id,row=req.row,col=req.col,chess=stone,
                next_player_id=turn,turn_deadline=deadline,is_win=win)
    notify(key,players,MsgId.GomokuOnlinePlayerPlaceStoneNotice,notice)
    if ended:
        finish(key,players,(state,first,turn,deadline,cells),pid if win else 0,
               pb.EGomokuOverReason_Win if win else pb.EGomokuOverReason_None)
    else:
        save(key,PLAYING,first,turn,deadline,cells)
    return OK


solo = partial(run,operation=settle_solo)
