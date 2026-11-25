"""Local Windows handoff between the private account launcher and its own direct Unity child."""
import ctypes
from ctypes import wintypes as w
from dataclasses import replace
from functools import partial
import ipaddress
import os
from pathlib import Path
import socket
import struct
import threading
import time
from realm_accounts import AccountError


def connection_pid(sock):
    peer_addr,peer_port=sock.getpeername()[:2]
    local_addr,local_port=sock.getsockname()[:2]
    if sock.family!=socket.AF_INET or not all(ipaddress.ip_address(addr).is_loopback for addr in (peer_addr,local_addr)):
        raise ValueError("Expected private loopback connection")
    get=ctypes.WinDLL("iphlpapi",use_last_error=True).GetExtendedTcpTable
    get.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.DWORD),w.BOOL,w.ULONG,w.ULONG,w.ULONG]
    get.restype=w.DWORD
    size=w.DWORD()
    for _ in range(4):
        buffer=ctypes.create_string_buffer(size.value) if 0<size.value<=16*1024*1024 else None
        result=get(buffer,ctypes.byref(size),False,socket.AF_INET,5,0)
        if result==122:
            continue
        if result or buffer is None:
            break
        data=buffer.raw[:size.value]
        count=struct.unpack_from("<I",data)[0]
        for state,src,sport,dst,dport,pid in struct.iter_unpack("<IIIIII",data[4:4+24*count]):
            if state==5 and pid and socket.ntohs(sport)==peer_port and socket.ntohs(dport)==local_port and socket.inet_ntoa(struct.pack("<I",src))==peer_addr and socket.inet_ntoa(struct.pack("<I",dst))==local_addr:
                return pid
        break
    raise ValueError("Private client connection not found")


def process_info(pid):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    open_process=kernel.OpenProcess
    open_process.argtypes=[w.DWORD,w.BOOL,w.DWORD]
    open_process.restype=w.HANDLE
    handle=open_process(0x1000,0,pid)
    if not handle:
        raise ValueError("Private client is not running")
    close=kernel.CloseHandle
    close.argtypes=[w.HANDLE]
    class Basic(ctypes.Structure):
        _fields_=[("reserved1",ctypes.c_void_p),("peb",ctypes.c_void_p),("reserved2",ctypes.c_void_p*2),
                  ("pid",ctypes.c_size_t),("parent",ctypes.c_size_t)]
    try:
        path=ctypes.create_unicode_buffer(32768)
        size=w.DWORD(len(path))
        image=kernel.QueryFullProcessImageNameW
        image.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        if not image(handle,0,path,ctypes.byref(size)):
            raise ValueError("Private client path unavailable")
        gettimes=kernel.GetProcessTimes
        gettimes.argtypes=[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4
        start,end,kern,user=[w.FILETIME() for _ in range(4)]
        if not gettimes(handle,*(ctypes.byref(t) for t in (start,end,kern,user))):
            raise ValueError("Private client creation time unavailable")
        basic=Basic()
        query=ctypes.WinDLL("ntdll").NtQueryInformationProcess
        query.argtypes=[w.HANDLE,w.ULONG,ctypes.c_void_p,w.ULONG,ctypes.c_void_p]
        if query(handle,0,ctypes.byref(basic),ctypes.sizeof(basic),None)!=0:
            raise ValueError("Private launcher parent unavailable")
        return Path(path.value).resolve(), int(basic.parent), (start.dwHighDateTime<<32)|start.dwLowDateTime
    finally:
        close(handle)


class NativeAccountSeats:
    def __init__(self,identity,client):
        self.identity,self.client=identity,client
        self.lock=threading.Lock()
        self.pending={}
        self.remote=None
        self.picker=None
        self.last_owner=None
        self.disconnected_owner=None

    @classmethod
    def configured(cls,identity,bind):
        if os.environ.get("OF_ACCOUNT_NATIVE","0")!="1":
            return None
        exe=Path(os.environ.get("OF_PRIVATE_CLIENT_EXE",""))
        if os.name!="nt" or not ipaddress.ip_address(bind).is_loopback or not exe.is_absolute():
            raise ValueError("Native account binding requires the local private Windows client")
        path=exe.resolve(strict=True)
        if path.name.lower()!="launcher.exe" or not (path.parent/".of-private-client.json").is_file():
            raise ValueError("Choose the prepared private game executable")
        seats=cls(identity,path)
        from local_picker import LocalPicker
        picker=LocalPicker.configured(path.parent,os.environ.get("OF_DISPATCH_URL",""))
        if picker and picker.executable.name in ("OF_SF 登陆器.exe","OF_SF 玩家补丁.exe"):
            seats.picker=picker
        return seats

    def attach(self,sock,body):
        required={"ticket","pid","slot"} if self.remote else {"ticket","pid"}
        if set(body)!=required or type(body["pid"]) is not int or not 0<body["pid"]<2**32:
            raise AccountError(400,"invalid_request")
        try:
            parent=connection_pid(sock)
            pid=body["pid"]
            exe,ppid,born=process_info(pid)
            if self.client!=exe or ppid!=parent or process_info(parent)[2]>=born:
                raise ValueError("Unrelated local game")
            with self.lock:
                now=time.monotonic()
                self.pending={p:e for p,e in self.pending.items() if e[2]>now}
                if pid in self.pending or len(self.pending)>=64:
                    raise ValueError("Duplicate or busy")
                if self.remote:
                    slot=self.remote.reserve(body["slot"],body["ticket"])
                else:
                    slot=self.identity.resolve_ticket(body["ticket"])
                    with self.identity._write() as db:
                        if not db.execute("SELECT 1 FROM realm_accounts WHERE slot=?",(slot,)).fetchone():
                            raise ValueError("Not a password account")
                self.pending[pid]=(born,slot,now+300)
                self.last_owner=self.disconnected_owner=None
        except (ValueError,OSError):
            raise AccountError(403,"invalid_native_handoff") from None

    def claim(self,sock):
        pid=connection_pid(sock)
        exe,_,born=process_info(pid)
        with self.lock:
            entry=self.pending.get(pid)
            if not entry or exe!=self.client or born!=entry[0] or entry[2]<=time.monotonic():
                if self.picker and exe==self.client and self.disconnected_owner==(pid,born):
                    try:
                        if self.picker.enter(sock,reconnect=True):
                            self.disconnected_owner=None
                    except OSError:
                        pass
                raise ValueError("No matching private account launch")
            del self.pending[pid]
            if self.remote and self.picker:
                self.last_owner=(pid,born)
                return replace(entry[1],on_disconnect=partial(self._disconnected,self.last_owner))
            return entry[1]

    def _disconnected(self,owner):
        with self.lock:
            if self.last_owner==owner:
                self.disconnected_owner=owner
