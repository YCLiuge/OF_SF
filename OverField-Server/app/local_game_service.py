"""Privately hosted of-ps gameplay with distinct realm-local identity."""
import importlib
import logging
import queue
from pathlib import Path
import socket
import socketserver
import sqlite3
import struct
import sys
from threading import BoundedSemaphore, Condition, Event, Lock, RLock, Thread
import time

UPSTREAM = Path(__file__).resolve().parent / "upstream"
sys.path.insert(0, str(UPSTREAM))

from google.protobuf.message import DecodeError
from config import Config
from network.msg_id import MsgId
from proto import net_pb2 as pb
from server import notice_sync, scene_data
from utils import db, res_loader
from local_game_wire import encode, receive
import realm_rules
import realm_uids
from online_identity import OnlineIdentity
from remote_realm import RemoteRealm
from contextlib import ExitStack

_log = logging.getLogger("overfield.local.game")
_runtime_owner = Lock()


class Session:
    def __init__(self, sock, slot, pid, name):
        self.sock=sock
        self.slot=slot
        self.player_id=pid
        self.player_name=name
        self.scene_id=9999
        self.channel_id=self.chat_channel_id=1
        self.avatar_id=41101
        self.badge_id=0
        self.scene_player=pb.ScenePlayer()
        self.home_seat=None
        self.pos={}
        self.drop_items={}
        self.color_data=[None,None,pb.PosColor()]
        self.fishing=False
        self.dungeon=[0,0,0,-1]
        self.quests={}
        self.running=True
        self.logged_in=self.remote=False
        self.responses=[]
        self.commit_callbacks=[]
        self.pending_publish=None
        self.scene_membership=None
        self.sequence=1
        self.outbox=queue.Queue(128)
        self.outstanding=0
        self.out_lock=Lock()
        self.writer=None

    def start_sender(self):
        self.writer=Thread(target=self._send_loop,daemon=True,name="private-game-writer")
        self.writer.start()

    def _send_loop(self):
        while self.running:
            try:frame=self.outbox.get(timeout=0.5)
            except queue.Empty:continue
            try:self.sock.sendall(frame)
            except OSError:
                self.disconnect()
                break
            finally:
                with self.out_lock:self.outstanding-=len(frame)

    def publish(self):
        if self.pending_publish is not None:
            self.pending_publish.discard(self)
        frames,self.responses=self.responses,[]
        for frame in frames:
            with self.out_lock:
                if self.outstanding+len(frame)>6*1024*1024:
                    self.disconnect()
                    return
                self.outstanding+=len(frame)
            try:self.outbox.put_nowait(frame)
            except queue.Full:
                self.disconnect()
                return

    def disconnect(self):
        self.running=False
        try:self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.sock.close()

    def send(self,msg_id,message,packet_id,is_bin=False):
        if not is_bin and getattr(message, "status", 0) > pb.StatusCode_OK and hasattr(self, "stages"):
            self.stages.failure(msg_id, message.status)
            self.stages.failed_operation(getattr(self, "diagnostic_request", None), msg_id, message.status)
        data=message if is_bin else realm_uids.outgoing(message).SerializeToString()
        if len(self.responses)>=512:
            raise ValueError("too many responses")
        n=0 if msg_id in (1002,1004,1006,1008) else self.sequence
        frame=encode(msg_id,data,packet_id,n)
        if not self.responses:
            self.response_sequence=self.sequence
        self.responses.append(frame)
        if n:
            self.sequence=1 if n==0xffffffff else n+1
        if self.pending_publish is not None:
            self.pending_publish.add(self)


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads=False
    allow_reuse_address=sys.platform!='win32'
    def server_bind(self):
        if sys.platform=='win32' and hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()

    def service_actions(self):
        super().service_actions()
        # The accept loop also ticks while every player is idle.
        now = time.monotonic()
        if self.owner.campfires.next_expiry is not None:
            with self.owner.lock:
                try:
                    if self.owner.campfires.expire():
                        self.owner._publish_pending()
                except sqlite3.Error:
                    _log.exception("Could not expire campfires, will retry")
        if now >= self.owner.next_pose_flush:
            with self.owner.lock:
                try:
                    self.owner._flush_poses()
                except sqlite3.Error:
                    _log.exception("Could not checkpoint player movement, will retry")
                for pid in tuple(self.owner.pending_reclaims):
                    try:
                        self.owner._reclaim_departed(pid)
                    except Exception:
                        _log.exception("Could not reclaim disconnected player's furniture, will retry")
                self.owner.next_pose_flush = now + self.owner.pose_flush_interval

    def process_request(self,request,client_address):
        if self.owner.slots.acquire(blocking=False):
            try:
                with self.owner.connections_lock:
                    self.owner.connections.add(request)
                super().process_request(request,client_address)
            except BaseException:
                with self.owner.connections_lock:
                    self.owner.connections.discard(request)
                self.owner.slots.release()
                raise
        else:
            self.shutdown_request(request)

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            with self.owner.connections_lock:
                self.owner.connections.discard(request)
            self.owner.slots.release()


from stage_diagnostics import StageDiagnostics
from unsupported_requests import status_replies, unsupported_status


class _Connection(socketserver.BaseRequestHandler):
    def handle(self):
        session=None
        remote_player=None
        remote_resources=ExitStack()
        host=self.server.owner
        host.stages.connection(True)
        close_reason = 'server_stop'
        try:
            self.request.settimeout(8)
            head, body = receive(self.request)
            host.stages.message(head.msg_id)
            if head.msg_id != MsgId.VerifyLoginTokenReq:
                close_reason = 'protocol'
                return
            req = pb.VerifyLoginTokenReq.FromString(body)
            try:
                remote_grant=None
                if req.login_token.startswith("oft_") or host.online_identity is None:
                    if host.remote_password:
                        raise ValueError("Use the private launcher to select the remote account")
                    slot = host.identity.resolve_ticket(req.login_token)
                    if req.sdk_uid and req.sdk_uid != slot:
                        raise ValueError("wrong private profile")
                    reply_uid, reply_account, reply_os = slot, 0, 0
                else:
                    if host.native_accounts:
                        host.online_identity.verify_channel(req.sdk_uid,req.login_token,self.client_address[0])
                        selected = host.native_accounts.claim(self.request)
                        if host.remote_password:
                            remote_grant=selected
                            slot=selected.slot
                        else:
                            slot=selected
                    else:
                        slot = host.online_identity.authenticate(req.sdk_uid, req.login_token, self.client_address[0])
                    reply_uid, reply_account, reply_os = req.sdk_uid, req.account_type, req.os
                if remote_grant:
                    remote_player=remote_resources.enter_context(host.remote_password.connect(remote_grant))
                elif host.remote_realm:
                    remote_player=remote_resources.enter_context(host.remote_realm.connect(slot))
                else:
                    session=host._open(self.request,slot,host.identity.profile_name(slot))
            except ValueError:
                host.stages.verified(False)
                close_reason = 'verify_rejected'
                failure = pb.VerifyLoginTokenRsp(status=pb.StatusCode_FAIL)
                self.request.sendall(encode(MsgId.VerifyLoginTokenRsp, failure.SerializeToString(), head.packet_id))
                return
            from private_chat_text import ProtectedChat
            (remote_player or session).chat_guard=ProtectedChat(req.sdk_uid,req.login_token,slot)
            host.stages.verified(True)
            del req, body
            verified = pb.VerifyLoginTokenRsp(status=pb.StatusCode_OK, user_id=(remote_player.player_id if remote_player else realm_uids.public_uid(session.player_id)),
                                              sdk_uid=reply_uid, account_type=reply_account, os=reply_os, is_server_open=True)
            self.request.sendall(encode(MsgId.VerifyLoginTokenRsp, verified.SerializeToString(), head.packet_id))
            if remote_player is not None:
                remote_player.play(self.request,host)
                return
            # Unity can resume private preferences immediately after token verification.
            deadline = time.monotonic() + 8
            archive_reads = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                self.request.settimeout(remaining)
                head, body = receive(self.request)
                host.stages.message(head.msg_id)
                if head.msg_id != MsgId.GetArchiveInfoReq:
                    break
                if archive_reads >= 32:
                    close_reason = "protocol"
                    return
                archive_reads += 1
                with host.lock:
                    with db.conn:
                        host.handlers[MsgId.GetArchiveInfoReq].handle(session, body, head.packet_id)
                    frames, session.responses = session.responses, []
                    host.pending_publish.discard(session)
                for frame in frames:
                    self.request.sendall(frame)
            if head.msg_id != MsgId.PlayerLoginReq:
                close_reason = 'protocol'
                return
            pb.PlayerLoginReq.FromString(body)
            info = db.get_players_info(session.player_id, "register_time,server_time_zone")
            login = pb.PlayerLoginRsp(status=pb.StatusCode_OK, server_time_ms=int(time.time()*1000),
                                      region_name="private", player_name="" if session.needs_name else session.player_name,
                                      register_time=info[0], server_time_zone=info[1],
                                      scene_id=session.scene_id, channel_id=session.channel_id)
            self.request.sendall(encode(MsgId.PlayerLoginRsp, login.SerializeToString(), head.packet_id))
            session.start_sender()
            self.request.settimeout(120)
            while not host.closing.is_set():
                header,body=receive(self.request)
                host.stages.message(header.msg_id)
                if header.msg_id not in host.handlers and (
                        not session.logged_in or header.msg_id not in host.failure_replies):
                    close_reason = "unsupported_message"
                    return
                try:
                    host._handle(session,header.msg_id,header.packet_id,body)
                except DecodeError:
                    close_reason = "handler_decode"
                    return
                except (ValueError, KeyError, IndexError, TypeError):
                    close_reason = "handler_data"
                    return
                except sqlite3.Error:
                    close_reason = "handler_storage"
                    return
                except Exception:
                    close_reason = "handler_error"
                    return
        except TimeoutError:
            close_reason = "timeout"
        except EOFError:
            close_reason = "eof"
        except (ConnectionError,OSError):
            close_reason = "transport"
        except (ValueError,DecodeError,KeyError,IndexError):
            close_reason = "protocol"
        except Exception:
            close_reason = "server_error"
        finally:
            host.stages.closed_because(close_reason)
            remote_resources.close()
            host.stages.connection(False)
            if session is not None:
                session.disconnect()
                host._leave(session)
                if session.writer is not None:session.writer.join()


class LocalGameService:
    """One packaged realm per process. A separate file-backed identity service issues tickets."""
    def __init__(self,identity,data_dir,resources,bind="127.0.0.1",port=11033):
        self.identity=identity
        self.data_dir=Path(data_dir).resolve()
        self.stages=StageDiagnostics(self.data_dir)
        self.realm_rules=realm_rules.load(identity)
        self.resources=Path(resources).resolve()
        self.online_identity=OnlineIdentity.configured(identity,bind)
        from native_account_seats import NativeAccountSeats
        self.native_accounts=NativeAccountSeats.configured(identity,bind)
        if self.native_accounts and not self.online_identity:
            raise ValueError("The retail Windows client requires its configured channel verifier")
        from remote_password import RemotePassword
        self.remote_password=RemotePassword.configured(identity,bind)
        self.remote_realm=None if self.remote_password else RemoteRealm.configured(identity,bind)
        if self.native_accounts:
            self.native_accounts.remote=self.remote_password
        self.bind=bind
        self.port=port
        self.slots=BoundedSemaphore(24)
        self.lock=RLock()
        self.slot_available=Condition(self.lock)
        self.connections_lock=Lock()
        self.connections=set()
        self.closing=Event()
        self.active={}
        self.scene_members={}
        self.pending_publish=set()
        self.pending_poses={}
        self.pending_dungeon_poses={}
        self.pending_reclaims={}
        self.pose_flush_interval=1.0
        self.listener=None
        self.thread=None
        self.handlers={}
        self.failure_replies=status_replies()

    def start(self):
        if self.listener is not None or not _runtime_owner.acquire(blocking=False):
            raise RuntimeError("A game realm is already running in this process")
        try:
            self.closing.clear()
            self.next_pose_flush=time.monotonic()+self.pose_flush_interval
            self.data_dir.mkdir(parents=True,exist_ok=True)
            res_loader.init(self.resources)
            Config.DB_PATH=str(self.data_dir/"game.sqlite")
            from realm_accounts import initialize
            initialize(self.identity)
            db.init()
            db.conn.execute("ATTACH DATABASE ? AS authority",(str(self.identity.path),))
            db.conn.executescript("""
                CREATE TABLE IF NOT EXISTS private_slots (
                    player_id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS private_poses (
                    player_id INTEGER NOT NULL, scene INTEGER NOT NULL, pos BLOB NOT NULL, rot BLOB NOT NULL,
                    PRIMARY KEY(player_id,scene));
                CREATE TABLE IF NOT EXISTS player_kv (
                    player_id INTEGER NOT NULL, ns TEXT NOT NULL, k TEXT NOT NULL, v BLOB,
                    PRIMARY KEY(player_id,ns,k), FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS claimed_rewards (
                    player_id INTEGER NOT NULL, system TEXT NOT NULL, key TEXT NOT NULL, claim_time INTEGER DEFAULT 0,
                    PRIMARY KEY(player_id,system,key), FOREIGN KEY(player_id) REFERENCES players(player_id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS supply_box (
                    player_id INTEGER PRIMARY KEY REFERENCES players(player_id), next_reward_time INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS private_wishes (
                    player_id INTEGER NOT NULL REFERENCES players(player_id), shop INTEGER NOT NULL,
                    grid INTEGER NOT NULL, pool INTEGER NOT NULL, created INTEGER NOT NULL,
                    PRIMARY KEY(player_id,shop,grid));
                CREATE TABLE IF NOT EXISTS private_boxes (
                    player_id INTEGER NOT NULL REFERENCES players(player_id), zone INTEGER NOT NULL, dynamic INTEGER NOT NULL,
                    idx INTEGER NOT NULL, template INTEGER NOT NULL, data BLOB, refresh INTEGER DEFAULT 0, claimed INTEGER DEFAULT 0,
                    PRIMARY KEY(player_id,zone,dynamic,idx));
                CREATE TABLE IF NOT EXISTS private_locations (
                    player_id INTEGER PRIMARY KEY, scene INTEGER NOT NULL);
            """)
            from private_campfires import Campfires
            self.campfires = Campfires(self._broadcast_scene)
            from private_inventory import initialize as setup_inventory
            setup_inventory()
            from private_mp_teams import initialize as setup_mp_teams
            setup_mp_teams()
            from private_gacha import setup as setup_gacha
            setup_gacha()
            from private_intervals import setup as setup_intervals
            setup_intervals()
            from private_friends import setup as setup_friends
            setup_friends()
            from private_chat import setup as setup_chat
            setup_chat()
            with db.conn:
                realm_uids.initialize(db.conn,self.realm_rules.uid_start)
            from private_channels import setup as setup_channels
            setup_channels(self.realm_rules)
            self.pending_reclaims.clear()  # Startup has reclaimed all offline public placements.
            self._flush_poses()  # Retry a failed final checkpoint when restarting the same realm.
            from private_activity_rewards import setup as setup_activity_rewards
            setup_activity_rewards()
            from private_entitlements import catalog, migrate
            self.entitlements = catalog()
            migrate(db.conn,self.entitlements)
            self.scene_ids={r["ID"] for r in res_loader.res["ScenesConfigAsset"]["Scenes"] if r.get("Born")}
            self.initial_scene = int(next(row["values"][0] for row in res_loader.res["GlobalConfig"]["config"]["datas"]
                                          if row["key"] == "Player_Initial_Scenario_ID"))
            if self.initial_scene not in self.scene_ids:
                raise ValueError("Initial scene lacks an authored player spawn")
            for f in sorted((UPSTREAM/"handlers").glob("*.py")):
                if f.stem.startswith('_'):continue
                handler=importlib.import_module("handlers."+f.stem).Handler()
                self.handlers[handler.msg_id]=handler
            self.listener=_TCP((self.bind,self.port),_Connection)
            self.listener.owner=self
            self.port=self.listener.server_address[1]
            self.thread=Thread(target=self.listener.serve_forever,daemon=True,name="private-game")
            self.thread.start()
        except BaseException:
            if self.listener is not None:
                self.listener.server_close(); self.listener=None
            if db.conn is not None:
                db.conn.close();db.conn=None
            _runtime_owner.release()
            raise

    def _open(self,sock,slot,name):
        if not isinstance(name,str) or not name.strip():
            raise ValueError("Private profile name must be provided")
        with self.slot_available:
            # A newly verified private ticket may arrive just before the previous worker observes FIN.
            # Do not take over or invalidate a live session.
            available = self.slot_available.wait_for(
                lambda: slot not in self.active or self.closing.is_set(), timeout=1.0)
            if not available or self.closing.is_set():
                raise ValueError("Profile already connected or server stopping")
            row=db.conn.execute("SELECT player_id FROM private_slots WHERE slot=?",(slot,)).fetchone()
            if row:
                # A failed disconnect checkpoint must complete before the next session reads its save.
                self._flush_poses(row[0])
                self._reclaim_departed(row[0])
            with db.conn:
                if row:
                    pid=row[0]
                else:
                    # Do not assign pre-existing, unrelated players from a database imported without its realm mapping.
                    pid=realm_uids.next_uid(db.conn)
                    if pid>=0xffffffff:raise ValueError("No free gameplay player ID")
                    db.conn.execute("INSERT INTO private_slots(player_id,slot) VALUES (?,?)",(pid,slot))
                account=db.conn.execute("SELECT player_name FROM players WHERE player_id=?",(pid,)).fetchone()
                if account is None:
                    db.init_player(pid)
                    db.conn.execute("INSERT INTO player_kv(player_id,ns,k,v) VALUES(?,'profile','bottle_intro','active')",(pid,))
                    db.conn.execute("INSERT OR IGNORE INTO private_locations(player_id,scene,channel) VALUES(?,?,?)",
                                    (pid,self.initial_scene,self.realm_rules.public_min))
                from private_entitlements import grant
                grant(db.conn,pid,self.entitlements)
                named = db.conn.execute("SELECT p.nickname FROM authority.profiles p WHERE slot=? AND (EXISTS(SELECT 1 FROM authority.realm_accounts a WHERE a.slot=p.slot) OR EXISTS(SELECT 1 FROM authority.realm_named_profiles n WHERE n.slot=p.slot))",(slot,)).fetchone()
                if named:
                    proposed = named[0]
                    db.conn.execute("UPDATE players SET player_name=? WHERE player_id=?",(proposed,pid))
                elif account is None or not (account[0] or '').strip():
                    proposed=name.strip()
                    suffix=0
                    while db.conn.execute("SELECT 1 FROM players WHERE player_id!=? AND player_name=?",(pid,proposed)).fetchone():
                        suffix+=1
                        ending="-"+str(pid)+(("-"+str(suffix)) if suffix>1 else "")
                        proposed=name.strip()[:16-len(ending)]+ending
                    db.conn.execute("UPDATE players SET player_name=? WHERE player_id=?",(proposed,pid))
                else:
                    proposed=account[0]
                from private_explore import grant_private_permits
                grant_private_permits(pid)
                s=Session(sock,slot,pid,proposed)
                s.pending_publish = self.pending_publish
                s.campfires = self.campfires
                s.entitlements = self.entitlements
                s.realm_rules = self.realm_rules
                s.stages = self.stages
                s.needs_name = bool(db.conn.execute("SELECT 1 FROM authority.realm_pending_names WHERE slot=?",(slot,)).fetchone())
                from private_channels import restore
                saved=db.conn.execute("SELECT scene,channel FROM private_locations WHERE player_id=?",(pid,)).fetchone()
                s.valid_scenes=self.scene_ids
                restore(s,saved,self.scene_ids)
            self.active[slot]=s
            scene_data._session_list.append(s)
            return s

    def _handle(self,s,msg_id,packet_id,body):
        if not self.stages.enabled or msg_id not in self.stages.variant_requests:
            return self._dispatch(s,msg_id,packet_id,body)
        s.diagnostic_request = (msg_id, body)
        try:
            return self._dispatch(s,msg_id,packet_id,body)
        finally:
            s.diagnostic_request = None

    def _dispatch(self,s,msg_id,packet_id,body):
        body=realm_uids.incoming(msg_id,body)
        if msg_id not in self.handlers:
            if not s.logged_in or msg_id not in self.failure_replies:
                raise ValueError("Unsupported gameplay command")
            request, reply, reply_id = self.failure_replies[msg_id]
            request.FromString(body)
            self.stages.unsupported(msg_id)
            with self.lock:
                s.send(reply_id,reply(status=unsupported_status(msg_id)),packet_id)
                s.publish()
            return
        if s.needs_name and msg_id not in (MsgId.ChangeNickNameReq, MsgId.PlayerPingReq, MsgId.GetArchiveInfoReq):
            pair=self.failure_replies.get(msg_id)
            if pair:
                with self.lock:
                    s.send(pair[2],pair[1](status=pb.StatusCode_BAD_REQ),packet_id)
                    s.publish()
                return
            raise ValueError("Complete the native name panel before gameplay")
        if not s.logged_in and msg_id not in (MsgId.PlayerMainDataReq,MsgId.PlayerPingReq) and not (s.needs_name and msg_id in (MsgId.ChangeNickNameReq,MsgId.GetArchiveInfoReq)):
            raise ValueError("Request main data before other gameplay")
        if msg_id==MsgId.ChangeSceneChannelReq and s.dungeon[0]:
            with self.lock:
                s.send(MsgId.ChangeSceneChannelRsp,pb.ChangeSceneChannelRsp(status=pb.StatusCode_BAD_REQ),packet_id)
                s.publish()
            return
        # Channel admission is transactional in the scene-switch handler.
        with self.lock:
            if msg_id in (MsgId.ChangeSceneChannelReq, MsgId.DungeonEnterReq, MsgId.DungeonExitReq, MsgId.DungeonRepeatEnterReq):
                # Scene construction reads committed poses, including switches to this same scene.
                self._flush_poses(s.player_id)
            previous_key = s.scene_membership
            try:
                with db.conn:
                    if s.logged_in and msg_id==MsgId.PlayerMainDataReq:
                        raise ValueError("Request main data once per session")
                    notice_sync.tod_time=int((21600+time.time()*50)%86400)
                    result = self.handlers[msg_id].handle(s,body,packet_id)
                    if msg_id in (MsgId.ChangeSceneChannelReq,MsgId.PlayerMainDataReq):
                        db.conn.execute("INSERT OR REPLACE INTO private_locations(player_id,scene,channel) VALUES(?,?,?)",(s.player_id,s.scene_id,s.channel_id))
                    key = (s.scene_id,s.channel_id) if s.logged_in and not s.dungeon[0] else None
                    self._index_scene(s,key)
                    self._drain_scene_events()
            except BaseException:
                s.commit_callbacks.clear()
                self._index_scene(s,previous_key)
                self._discard_pending()
                scene_data._scene_action.clear()
                scene_data._scene.clear()
                scene_data._rec_list.clear()
                raise
            if s.commit_callbacks:
                from private_request import flush_committed
                flush_committed(s)
            if msg_id==MsgId.PlayerSceneRecordReq and result is not None:
                self._movement(s,result)
            self._publish_pending()

    def _index_scene(self,s,key):
        previous = s.scene_membership
        if previous == key:
            return
        if previous is not None:
            group = self.scene_members[previous]
            group.remove(s)
            if not group:
                del self.scene_members[previous]
        if key is not None:
            self.scene_members.setdefault(key,set()).add(s)
        s.scene_membership=key

    def _discard_pending(self):
        for peer in self.pending_publish:
            peer.responses.clear()
            peer.sequence=peer.response_sequence
        self.pending_publish.clear()

    def _publish_pending(self):
        while self.pending_publish:
            peer=self.pending_publish.pop()
            if peer.running:
                peer.publish()

    def _movement(self,s,rec):
        if not (rec.HasField("pos") or rec.HasField("rot")):
            return
        lead = s.dungeon_lead if s.dungeon[0] else s.scene_player.team.char1
        if rec.HasField("pos"):
            lead.pos.CopyFrom(rec.pos)
        if rec.HasField("rot"):
            lead.rot.CopyFrom(rec.rot)
        pos,rot = lead.pos.SerializeToString(),lead.rot.SerializeToString()
        if s.dungeon[0]:
            self.pending_dungeon_poses[s.player_id]=(pos,rot,s.player_id,s.dungeon[0],s.dungeon_visit)
        else:
            if s.scene_id not in s.pos:
                s.pos[s.scene_id]=pb.Vector3()
            s.pos[s.scene_id].CopyFrom(lead.pos)
            self.pending_poses[s.player_id]=(s.player_id,s.scene_id,pos,rot)

    def _flush_poses(self,player_id=None):
        if not self.pending_poses and not self.pending_dungeon_poses:
            return
        tables=(
            (self.pending_poses,"INSERT OR REPLACE INTO private_poses VALUES(?,?,?,?)"),
            (self.pending_dungeon_poses,"UPDATE private_dungeon_poses SET pos=?,rot=? WHERE player=? AND dungeon=? AND visit=?"),
        )
        with db.conn:
            for pending,sql in tables:
                if player_id is None and pending:
                    db.conn.executemany(sql,pending.values())
                elif player_id in pending:
                    db.conn.execute(sql,pending[player_id])
        for pending,_ in tables:
            if player_id is None:
                pending.clear()
            else:
                pending.pop(player_id,None)

    def _broadcast_scene(self,key,msg_id,notice):
        recipients=self.scene_members.get(key)
        if not recipients:
            return
        data=realm_uids.outgoing(notice).SerializeToString()
        for peer in recipients:
            if peer.running:
                peer.send(msg_id,data,0,is_bin=True)

    def _drain_scene_events(self):
        for sid,channels in scene_data._scene_action.items():
            for channel,events in channels.items():
                for notice in events:
                    self._broadcast_scene((sid,channel),MsgId.ServerSceneSyncDataNotice,notice)
        scene_data._scene_action.clear()
        for sid,ch in scene_data._rec_list:
            channels=scene_data._scene[sid]
            changed=channels.pop(ch)
            if not channels:
                del scene_data._scene[sid]
            notice=pb.PlayerSceneSyncDataNotice(status=pb.StatusCode_OK)
            for pid,record in changed.items():
                item=notice.data.add();item.player_id=pid;item.data.add().CopyFrom(record)
            self._broadcast_scene((sid,ch),MsgId.PlayerSceneSyncDataNotice,notice)
        scene_data._rec_list.clear()

    def _reclaim_departed(self, pid):
        if pid not in self.pending_reclaims:
            return
        from private_channels import reclaim
        try:
            with db.conn:
                reclaim(pid,self.pending_reclaims[pid],notify_peers=True)
        except BaseException:
            self._discard_pending()
            raise
        del self.pending_reclaims[pid]
        self._publish_pending()

    def _leave(self,s):
        with self.lock:
            s.running=False
            previous=s.scene_membership
            self._index_scene(s,None)
            self.pending_publish.discard(s)
            if self.active.get(s.slot) is s:
                self.active.pop(s.slot)
                self.slot_available.notify_all()
            if s in scene_data._session_list:scene_data._session_list.remove(s)
            try:
                self._flush_poses(s.player_id)
            except sqlite3.Error:
                _log.exception("Could not checkpoint disconnected player's pose, will retry")
            if s.logged_in:
                from private_channels import is_public
                channel=previous[1] if previous is not None else s.channel_id
                if is_public(s.realm_rules,channel):
                    self.pending_reclaims[s.player_id]=channel
                    try:
                        self._reclaim_departed(s.player_id)
                    except Exception:
                        _log.exception("Could not reclaim disconnected player's furniture, will retry")
            if previous is not None and not self.closing.is_set():
                notice=pb.ServerSceneSyncDataNotice(status=pb.StatusCode_OK)
                entry=notice.data.add(player_id=s.player_id)
                entry.server_data.add(action_type=pb.SceneActionType_LEAVE)
                self._broadcast_scene(previous,MsgId.ServerSceneSyncDataNotice,notice)
            self._publish_pending()

    def stop(self):
        if self.listener is None:return
        self.closing.set()
        self.listener.shutdown()
        with self.connections_lock:
            for sock in list(self.connections):
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                # Winsock select on a socket with a receive timeout wakes on close.
                sock.close()
        self.listener.server_close()
        self.thread.join()
        with self.lock, ExitStack() as cleanup:
            cleanup.callback(_runtime_owner.release)
            cleanup.callback(setattr,self,"listener",None)
            cleanup.callback(setattr,self,"thread",None)
            cleanup.callback(setattr,db,"conn",None)
            cleanup.callback(db.conn.close)
            cleanup.callback(db.db._executor.shutdown,wait=True,cancel_futures=True)
            scene_data._session_list.clear();scene_data._scene.clear()
            scene_data._scene_action.clear();scene_data._rec_list.clear()
            self.scene_members.clear()
            self.pending_publish.clear()
            self._flush_poses()
            for pid in tuple(self.pending_reclaims):
                self._reclaim_departed(pid)
            db.conn.commit()
