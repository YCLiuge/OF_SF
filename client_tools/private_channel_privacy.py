"""Protect the private copy's channel diagnostic log without reading account data."""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


def _reject_redirect(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("私服目录中发现重定向路径，已停止处理渠道日志。")


def _unlink_readonly_windows(target):
    """Unlink this name without changing attributes on any of the other hardlinks."""
    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = dll.CreateFileW
    open_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    open_file.restype = wintypes.HANDLE
    set_info = dll.SetFileInformationByHandle
    set_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    set_info.restype = wintypes.BOOL
    close = dll.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = open_file(str(target), 0x10000, 7, None, 3, 0x80, None)  # DELETE, all sharing, OPEN_EXISTING
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        flags = wintypes.DWORD(0x01 | 0x02 | 0x10)  # delete, POSIX semantics, ignore read-only attribute
        if not set_info(handle, 21, ctypes.byref(flags), ctypes.sizeof(flags)):  # FileDispositionInfoEx
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def protect_private_channel_log(home):
    """Return True when the private channel log was replaced by an empty read-only file."""
    home = Path(home).absolute()
    client = home / "Client"
    data = client / "launcher_Data"
    sdk = data / "Quicksdk"
    log = sdk / "qgpc_log.txt"
    marker = client / ".of-private-client.json"
    config = home / "client-config.json"
    product = data / "app.info"
    for path in (home, client, data, sdk, log, marker, config, product):
        _reject_redirect(path)
    if not all(path.is_file() for path in (marker, config, product)):
        raise ValueError("请选择已安装的私服根目录。")
    owner = json.loads(marker.read_bytes()).get("instance")
    product_lines = product.read_text("utf-8").splitlines()
    if (not isinstance(owner, str) or not owner or json.loads(config.read_bytes()).get("instance") != owner
            or len(product_lines) != 2 or product_lines[0] != "OFPriv" or not product_lines[1].strip()):
        raise ValueError("私服副本标识不匹配，已停止处理渠道日志。")
    sdk.mkdir(exist_ok=True)
    try:
        info = log.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("私服渠道诊断日志的位置不是普通文件。")
        if info.st_size == 0 and info.st_nlink == 1 and not info.st_mode & 0o222:
            return False
    fd, name = tempfile.mkstemp(prefix=".private-log-", dir=sdk)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            os.replace(temporary, log)
        except PermissionError:
            if os.name != "nt" or info is None or info.st_mode & stat.S_IWUSR:
                raise
            _unlink_readonly_windows(log)
            os.replace(temporary, log)
    except OSError as exc:
        raise OSError("无法保护私服渠道诊断日志，请关闭私服客户端后重试。") from exc
    finally:
        if temporary.exists():
            # Only change attributes of the fresh private temporary file, never of the old log.
            temporary.chmod(stat.S_IWUSR | stat.S_IRUSR)
            temporary.unlink()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清空并保护私服副本的渠道诊断日志")
    parser.add_argument("--home", type=Path, required=True, help="含 client-config.json 的私服目录")
    args = parser.parse_args()
    try:
        changed = protect_private_channel_log(args.home)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print("私服渠道日志已清空并设为只读。" if changed else "私服渠道日志已受保护。")
