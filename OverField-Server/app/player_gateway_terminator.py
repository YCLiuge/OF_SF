"""Accept Windows loopback traffic from one operator-managed TLS terminator process."""
import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
import socket
import struct
import sys


@lru_cache(maxsize=1)
def windows_api():
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    time_ptr=ctypes.POINTER(wintypes.FILETIME)
    kernel.GetProcessTimes.argtypes=(wintypes.HANDLE,time_ptr,time_ptr,time_ptr,time_ptr)
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    tcp=ctypes.WinDLL("iphlpapi",use_last_error=True).GetExtendedTcpTable
    tcp.argtypes=(ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),wintypes.BOOL,
                  wintypes.ULONG,wintypes.DWORD,wintypes.ULONG)
    tcp.restype=wintypes.DWORD
    return kernel,tcp


def start_time(pid):
    kernel,_=windows_api()
    process=kernel.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        times=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process,*(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(process)


def client_process(client,listener):
    _,tcp=windows_api()
    size=wintypes.DWORD()
    buffer=None
    for _ in range(4):
        status=tcp(buffer,ctypes.byref(size),False,socket.AF_INET,5,0)  # TCP_TABLE_OWNER_PID_ALL
        if status==0:
            break
        if status!=122 or size.value>32*1024*1024:  # ERROR_INSUFFICIENT_BUFFER
            return None
        buffer=ctypes.create_string_buffer(size.value)
    else:
        return None
    if buffer is None:
        return None
    rows=memoryview(buffer).cast('B')
    length,=struct.unpack_from('<I',rows)
    row=struct.Struct('<6I')
    if 4+length*row.size>len(rows):
        return None
    # Look up the client-owned outbound socket, not the gateway's accepted socket.
    for state,local_ip,local_port,remote_ip,remote_port,pid in row.iter_unpack(rows[4:4+length*row.size]):
        if (state==5 and local_ip==remote_ip==0x0100007f
                and socket.ntohs(local_port)==client[1] and socket.ntohs(remote_port)==listener[1]):
            return pid
    return None


class Terminator:
    def __init__(self,host,identity_file):
        if sys.platform!='win32' or host!='127.0.0.1' or not Path(identity_file).is_absolute():
            raise ValueError("The managed TLS terminator requires Windows, 127.0.0.1 and an absolute process identity file")
        self.identity_file=Path(identity_file)

    def accepts(self,client,listener):
        if not client or not listener or client[0]!='127.0.0.1' or listener[0]!='127.0.0.1':
            return False
        try:
            with self.identity_file.open("rb") as stream:
                raw=stream.read(80)
            if len(raw)>=80:
                return False
            expected_pid,expected_time=map(int,raw.split())
            pid=client_process(client,listener)
            return bool(pid and pid==expected_pid and start_time(pid)==expected_time)
        except (OSError,ValueError):
            return False
