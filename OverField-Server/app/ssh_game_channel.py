"""Two dedicated OpenSSH direct-tcpip streams to a player's own remote loopback services."""
from contextlib import contextmanager
from pathlib import Path
import ipaddress
import re
import shutil
import socket
import subprocess
from threading import Thread

TARGET = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9._-]{0,252}\Z", re.ASCII)

def valid_target(value):
    if TARGET.fullmatch(value):
        return True
    user, sep, host = value.rpartition("@")
    if "%" in host or sep and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}", user):
        return False
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True

def port_value(value):
    if not re.fullmatch(r"[0-9]{1,5}",value or "") or not 1<=int(value)<=65535:
        raise ValueError("Invalid remote service port")
    return int(value)

def shutdown(stream):
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

class SshChannels:
    def __init__(self,target,ssh_port=None,config=None):
        if not valid_target(target):
            raise ValueError("Specify your SSH user@hostname or a known host alias")
        self.target=target
        self.binary=shutil.which("ssh")
        if not self.binary:
            raise ValueError("OpenSSH Client is required to reach the remote realm")
        self.options=["-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no",
                      "-o","PreferredAuthentications=publickey","-o","ForwardAgent=no","-o","ForwardX11=no","-o","PermitLocalCommand=no",
                      "-o","ControlPath=none","-o","ControlMaster=no","-o","ConnectTimeout=7",
                      "-o","ConnectionAttempts=1","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=2"]
        if ssh_port:
            self.options.extend(("-p",str(port_value(ssh_port))))
        if config:
            cfg=Path(config)
            if not cfg.is_absolute() or not cfg.is_file():
                raise ValueError("SSH configuration must be an existing absolute file path")
            self.options.extend(("-F",str(cfg)))

    @contextmanager
    def open(self,port):
        # Each child uses a single SSH forwarding channel. No local listening ports are created.
        command=[self.binary,*self.options,"-T","-W","127.0.0.1:"+str(port),"--",self.target]
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
        with _socketpair() as (client,worker):
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                     bufsize=0,creationflags=flags)
            def to_ssh():
                try:
                    while data:=worker.recv(65536):
                        view=memoryview(data)
                        while view:
                            written=process.stdin.write(view)
                            if not written:return
                            view=view[written:]
                except (OSError,ValueError):
                    pass
                finally:
                    try:process.stdin.close()
                    except OSError:pass
            def from_ssh():
                try:
                    while data:=process.stdout.read(65536):
                        worker.sendall(data)
                except (OSError,ValueError):
                    pass
                finally:
                    try:worker.shutdown(socket.SHUT_WR)
                    except OSError:pass
            threads=[Thread(target=f,daemon=True) for f in (to_ssh,from_ssh)]
            for thread in threads:thread.start()
            try:
                client.settimeout(14)
                yield client
            finally:
                shutdown(client)
                shutdown(worker)
                if process.poll() is None:
                    process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
                for thread in threads:thread.join(timeout=3)
                process.stdout.close()
                if not process.stdin.closed:process.stdin.close()

@contextmanager
def _socketpair():
    a,b=socket.socketpair()
    with a,b:
        yield a,b
