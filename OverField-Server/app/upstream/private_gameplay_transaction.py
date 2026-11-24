"""Atomic inventory changes for native gameplay commands on the host's connection."""
from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.pb_create import make_item


class Rejected(ValueError):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class StackChanges:
    """Keep absolute balances per request. Result entries can still show individual gains."""
    def __init__(self, player):
        self.player = player
        self.items = {}

    def change(self, template, delta):
        if template not in self.items:
            raw = db.get_item_detail(self.player, template)
            if not raw and delta < 0:
                raise Rejected(pb.StatusCode_ITEM_NOT_ENOUGH)
            self.items[template] = pb.ItemDetail.FromString(raw) if raw else make_item(template, 0, self.player)
        item = self.items[template].main_item
        if item.WhichOneof("item") != "base_item":
            raise Rejected(pb.StatusCode_CFG_NOT_FOUND)
        balance = item.base_item.num + delta
        if balance < 0:
            raise Rejected(pb.StatusCode_ITEM_NOT_ENOUGH)
        if balance > 0xffffffff:
            raise Rejected(pb.StatusCode_Item_Get_Fail)
        item.base_item.num = balance
        return item

    def flush(self, notice):
        for template, item in self.items.items():
            db.set_item_detail(self.player, item.SerializeToString(), template)
            notice.items.add().CopyFrom(item)


class GameplayWrite:
    def __init__(self, player):
        self.balances = StackChanges(player)
        self.pack = pb.PackNotice(status=pb.StatusCode_OK)
        self.notices = []
        self.after_commit = []


def run(session, request, response, operation):
    """Keep partial replies, notifications and runtime callbacks out of failed commands."""
    conn = db.conn
    # A lone SQLite SAVEPOINT implicitly commits on RELEASE. Leave commit to the dispatcher's
    # outer transaction, which also publishes the response only after that commit succeeds.
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT gameplay_write")
    pending = GameplayWrite(session.player_id)
    result = type(response)()
    try:
        status = operation(session, request, result, pending)
        if status != pb.StatusCode_OK:
            raise Rejected(status)
        pending.balances.flush(pending.pack)
    except BaseException as error:
        conn.execute("ROLLBACK TO gameplay_write")
        conn.execute("RELEASE gameplay_write")
        if isinstance(error, Rejected):
            return error.status
        raise
    conn.execute("RELEASE gameplay_write")
    response.CopyFrom(result)
    if pending.pack.items:
        session.send(MsgId.PackNotice, pending.pack, 0)
    for msg_id, notice in pending.notices:
        session.send(msg_id, notice, 0)
    session.commit_callbacks.extend(pending.after_commit)
    return pb.StatusCode_OK
