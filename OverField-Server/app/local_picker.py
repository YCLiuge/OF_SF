"""Offer an address editor only in the launching player's native loopback discovery list."""
import ipaddress
import os
from pathlib import Path
import subprocess
from threading import Lock
from urllib.parse import urlsplit


LABEL = "添加或更改服务器地址"


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class LocalPicker:
    # Unity displays loginUrl verbatim beside the name. The route is public and
    # readable; only the foreground private player can activate the local UI.
    prefix = "/settings"

    def __init__(self, executable, *, preselected=False):
        self.preselected = preselected
        self.executable = Path(executable)
        self.child = None
        self.lock = Lock()

    @classmethod
    def configured(cls, client_root, origin):
        if os.name != "nt" or os.environ.get("OF_NATIVE_PICKER") not in ("1", "launcher") or not client_root:
            return None
        home = Path(__file__).resolve().parents[2]
        exe = home / ("OF_SF 玩家补丁.exe" if (home / ".of-player-patch.json").is_file() else "开放空间私服.exe")
        if Path(client_root).resolve()!=home/"Client" or not exe.is_file() or not is_loopback(urlsplit(origin).hostname):
            return None
        return cls(exe, preselected=os.environ["OF_NATIVE_PICKER"] == "launcher")

    def local_client(self, handler):
        if not is_loopback(handler.client_address[0]) or not is_loopback(handler.server.server_address[0]):
            return False
        headers = handler.headers
        return not any(name in headers for name in ("Origin","Referer","Sec-Fetch-Site","Sec-Fetch-Mode",
                    "Forwarded","X-Forwarded-For","X-Real-IP","Authorization","Proxy-Authorization","Cookie"))

    def requesting_player_pid(self, connection):
        from native_account_seats import connection_pid
        try:
            tracked = int((self.executable.parent / ".launcher.pid").read_text().strip())
            if tracked > 0 and connection_pid(connection) == tracked:
                return tracked
        except (OSError, ValueError):
            pass
        return None

    def player_is_foreground(self, connection):
        """Match the HTTP peer to the foreground private Unity window."""
        if os.name != "nt":
            return False
        import ctypes
        import ctypes.wintypes as wt
        pid = self.requesting_player_pid(connection)
        if pid is None:
            return False
        u = ctypes.WinDLL("user32",use_last_error=True)
        k = ctypes.WinDLL("kernel32",use_last_error=True)
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = [wt.HWND,ctypes.POINTER(wt.DWORD)]
        foreground = wt.DWORD()
        u.GetWindowThreadProcessId(u.GetForegroundWindow(),ctypes.byref(foreground))
        if foreground.value != pid:
            return False
        class LastInput(ctypes.Structure):
            _fields_ = [("size",wt.UINT),("tick",wt.DWORD)]
        last = LastInput(ctypes.sizeof(LastInput),0)
        k.GetTickCount.restype = wt.DWORD
        if not u.GetLastInputInfo(ctypes.byref(last)) or ((k.GetTickCount()-last.tick)&0xffffffff)>30000:
            return False
        k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];k.OpenProcess.restype=wt.HANDLE
        k.CloseHandle.argtypes=[wt.HANDLE]
        k.QueryFullProcessImageNameW.argtypes=[wt.HANDLE,wt.DWORD,wt.LPWSTR,ctypes.POINTER(wt.DWORD)]
        proc=k.OpenProcess(0x1000,False,pid)
        if not proc:
            return False
        try:
            buf=ctypes.create_unicode_buffer(32768);size=wt.DWORD(len(buf))
            return bool(k.QueryFullProcessImageNameW(proc,0,buf,ctypes.byref(size))) and (
                Path(buf.value).resolve()==(self.executable.parent/"Client"/"launcher.exe").resolve())
        finally:
            k.CloseHandle(proc)

    def enter(self, connection, *, reconnect=False):
        # This action is bound to one fixed binary installed beside this player's client.
        with self.lock:
            if self.child is not None and self.child.poll() is None:
                return True
            if not self.player_is_foreground(connection):
                return False
            flag = "--reconnect" if reconnect else "--realm-picker"
            self.child = subprocess.Popen([str(self.executable),flag],cwd=self.executable.parent,
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
            return True
