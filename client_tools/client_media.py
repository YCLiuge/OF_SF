"""Read the preserved PC client ZIP directly, including from its original contiguous UDF extent."""
import hashlib
import io
from pathlib import Path
import shutil
import zipfile
from contextlib import contextmanager

IMAGE="OverField-1.1.12-LocalInstaller-Win64.iso"
ARCHIVE="OverField-TapTap-Client-Archive-2026-10-21.zip"
SIZE=27695309337
ARCHIVE_SHA="5714b33b494d74d599f76495e73ba3cc2c7976d16fe0c40558e27ffb148e53c1"
IMAGE_SIZE=27713863680
IMAGE_OFFSET=17995776

class MediaExtent(io.RawIOBase):
    def __init__(self,source,start,length):
        self.source=source
        self.start=start
        self.length=length
        self.position=0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self,offset,whence=0):
        if whence not in (0,1,2):
            raise ValueError("Invalid origin")
        position=(0,self.position,self.length)[whence]+offset
        if position<0:
            raise OSError("Seek before start of client media")
        self.position=position
        return position

    def readinto(self,buffer):
        count=min(len(buffer),max(0,self.length-self.position))
        if not count:
            return 0
        self.source.seek(self.start+self.position)
        n=self.source.readinto(memoryview(buffer)[:count])
        if n!=count:
            raise EOFError("安装媒体不完整。")
        self.position+=n
        return n

def locate(source,home):
    for p in (source,source/ARCHIVE,source/IMAGE,home/IMAGE,home.parent/IMAGE,home/ARCHIVE,home.parent/ARCHIVE):
        if p.name.casefold() in (ARCHIVE.casefold(),IMAGE.casefold()) and p.is_file():
            return p.resolve()
    return None

@contextmanager
def open_media(path,*,verify=True):
    path=Path(path).resolve(strict=True)
    is_image=path.name.casefold()==IMAGE.casefold()
    if path.stat().st_size!=(IMAGE_SIZE if is_image else SIZE):
        raise ValueError("原版客户端安装媒体大小不符。")
    with path.open("rb") as source,io.BufferedReader(MediaExtent(source,IMAGE_OFFSET if is_image else 0,SIZE),1024*1024) as image:
        if verify:
            if hashlib.file_digest(image,"sha256").hexdigest()!=ARCHIVE_SHA:
                raise ValueError("原版客户端安装媒体 SHA-256 校验失败。")
            image.seek(0)
        with zipfile.ZipFile(image) as archive:
            yield archive

