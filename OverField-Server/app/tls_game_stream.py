"""Expose independent game readers/writers while one thread owns all TLS operations."""
from contextlib import contextmanager
import select
import socket
import ssl
from threading import Thread

LIMIT = 256 * 1024
CHUNK = 64 * 1024


def relay(secured, bridge):
    incoming, outgoing = bytearray(), bytearray()
    secured.setblocking(False)
    bridge.setblocking(False)
    read_wants_write = write_wants_read = False
    writing = None
    peer_open = True
    try:
        while peer_open or incoming:
            receive = peer_open and len(incoming) < LIMIT
            send = bool(outgoing) and peer_open
            want_read = ([bridge] if peer_open and len(outgoing) < LIMIT else [])
            want_write = [bridge] if incoming else []
            if receive and not read_wants_write or send and write_wants_read:
                want_read.append(secured)
            if send and not write_wants_read or receive and read_wants_write:
                want_write.append(secured)
            pending = receive and not read_wants_write and secured.pending()
            readable, writable, _ = select.select(want_read, want_write, [], 0 if pending else 1)
            if bridge in writable:
                del incoming[:bridge.send(incoming)]
            if bridge in readable:
                data = bridge.recv(CHUNK)
                if not data:
                    break
                outgoing.extend(data)
            if send and (secured in readable if write_wants_read else secured in writable):
                try:
                    if writing is None:
                        writing = bytes(outgoing[:CHUNK])
                    count=secured.send(writing)
                    del outgoing[:count]
                    writing=None
                    write_wants_read = False
                except ssl.SSLWantReadError:
                    write_wants_read = True
                except ssl.SSLWantWriteError:
                    write_wants_read = False
            if receive and (pending or (secured in writable if read_wants_write else secured in readable)):
                try:
                    data = secured.recv(CHUNK)
                    read_wants_write = False
                    if data:
                        incoming.extend(data)
                    else:
                        peer_open = False
                except ssl.SSLWantReadError:
                    read_wants_write = False
                except ssl.SSLWantWriteError:
                    read_wants_write = True
    except (OSError, ValueError):
        pass
    finally:
        try:
            bridge.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


@contextmanager
def game_stream(secured):
    caller, bridge = socket.socketpair()
    with caller, bridge:
        caller.settimeout(14)
        worker = Thread(target=relay, args=(secured, bridge), name="of-player-tls", daemon=True)
        worker.start()
        try:
            yield caller
        finally:
            try:
                caller.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            worker.join(3)
