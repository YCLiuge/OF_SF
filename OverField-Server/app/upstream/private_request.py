"""Bind private gameplay operations to their native request and response messages."""
from collections import OrderedDict
from hashlib import blake2s

from network.msg_id import MsgId
from network.packet_handler import PacketHandler, packet_handler
from proto import net_pb2 as pb


def flush_committed(session):
    """The host calls this after the outer connection transaction commits, before publication."""
    callbacks, session.commit_callbacks = session.commit_callbacks, []
    for callback, args in callbacks:
        callback(*args)


def _remember(session, attribute, key, value, limit):
    cache = getattr(session, attribute, None)
    if cache is None:
        cache = OrderedDict()
        setattr(session, attribute, cache)
    cache[key] = value
    if len(cache) > limit:
        cache.popitem(last=False)


def cache_after_commit(session, attribute, key, value, limit=64):
    session.commit_callbacks.append((_remember, (session, attribute, key, value, limit)))


def set_after_commit(session, attribute, value):
    session.commit_callbacks.append((setattr, (session, attribute, value)))


def cached_mutation(session, name, data, packet_id, method):
    reply = getattr(pb, name + "Rsp")()
    cache = getattr(session, "mutation_receipts", None)
    key = (name, packet_id, blake2s(data).digest())
    if packet_id and cache is not None and key in cache:
        reply.ParseFromString(cache[key])
    else:
        request = getattr(pb, name + "Req").FromString(data)
        reply.status = method(session, request, reply)
        if packet_id and reply.status == pb.StatusCode_OK:
            cache_after_commit(session, "mutation_receipts", key, reply.SerializeToString())
    session.send(getattr(MsgId, name + "Rsp"), reply, packet_id)


def handler(name, update, *, replay=False):
    request = getattr(pb, name + "Req")
    response = getattr(pb, name + "Rsp")
    reply_id = getattr(MsgId, name + "Rsp")

    @packet_handler(getattr(MsgId, name + "Req"))
    class Handler(PacketHandler):
        def handle(self, session, data, packet_id):
            if replay:
                return cached_mutation(session, name, data, packet_id, update)
            result = response()
            result.status = update(session, request.FromString(data), result)
            session.send(reply_id, result, packet_id)

    return Handler
