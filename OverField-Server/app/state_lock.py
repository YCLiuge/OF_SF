"""Cross-platform single-owner lock for the server and its state migration tools."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat


class StateBusy(RuntimeError):
    """Another supervisor or migration process holds the state directory."""


def is_link_or_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@contextmanager
def exclusive_state(directory, *, purpose="server"):
    """Hold across every state access; do not delete the sibling lock file."""
    if purpose not in ("server", "profiles"):
        raise ValueError("Unknown state lock purpose")
    directory = Path(directory).absolute()
    if directory.is_symlink() or (directory.exists() and is_link_or_reparse(directory.lstat())):
        raise ValueError("The state directory must not be a link")
    directory = directory.resolve()
    if directory == directory.parent:
        raise ValueError("Use a dedicated directory for server state")
    directory.parent.mkdir(parents=True, exist_ok=True)
    path = directory.with_name("." + directory.name + "." + purpose + ".lock")
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise ValueError("Invalid state lock file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Invalid state lock file")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK):
                raise StateBusy("Server state is in use. Stop the server and retry.") from error
            raise
        # Windows supports locking past EOF; keep one byte once first held.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        yield directory
    finally:
        # Closing the handle releases either OS lock, including on abnormal exit.
        os.close(fd)

