"""Private per-character mailbox using the native MailState and five MailOperateType values."""
from private_request import cache_after_commit
import json
import time
from datetime import datetime, timezone

from network.msg_id import MsgId
from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res
from private_dungeons import _table, credit_player_experience
from private_shop import award, UnsupportedGoods
from private_pets import has_room
from utils.pb_create import make_item

READ = pb.MailOperateType_READ
DELETE = pb.MailOperateType_DELETE_ALL_READED
CLAIM = pb.MailOperateType_CLAIM
CLAIM_ALL = pb.MailOperateType_CLAIM_ALL
COLLECT = pb.MailOperateType_COLLECT
INSTANCE_TYPES = {"weapon": pb.EBagItemTag_Weapon, "armor": pb.EBagItemTag_Armor, "poster": pb.EBagItemTag_Poster,
                  "treasure": pb.EBagItemTag_TreasureMap, "pet": pb.EBagItemTag_Pet}


def deliver_published(session, *, notify=True):
    """Materialize eligible immutable campaigns and advance the durable cursor in one transaction."""
    from private_wishes import send_mail
    pid = session.player_id
    cursor, last = db.conn.execute("""SELECT coalesce((SELECT last_batch FROM realm_mail_cursor WHERE player_id=?),0),
        coalesce((SELECT max(id) FROM authority.realm_mail_batches),0)""", (pid,)).fetchone()
    if cursor >= last:
        return
    rows = db.conn.execute("""SELECT id,issued,payload FROM authority.realm_mail_batches
        WHERE id IN (
            SELECT batch FROM authority.realm_mail_recipients WHERE slot=? AND batch>? AND batch<=?
            UNION ALL SELECT id FROM authority.realm_mail_batches WHERE scope='ongoing' AND id>? AND id<=?)
        ORDER BY id""", (session.slot,cursor,last,cursor,last)).fetchall()
    now = int(time.time())
    for _, issued, raw in rows:
        letter = json.loads(raw)
        if now < issued + letter["days"] * 86400:
            send_mail(pid, letter["title"], letter["body"], letter["rewards"], datetime.fromtimestamp(issued, timezone.utc),
                      session if notify else None, sender=letter["sender"], overdue_days=letter["days"])
    db.conn.execute("""INSERT INTO realm_mail_cursor VALUES(?,?)
        ON CONFLICT(player_id) DO UPDATE SET last_batch=excluded.last_batch""", (pid,last))


def poll(session):
    now = time.monotonic()
    if now >= getattr(session,"mail_poll_at",0):
        deliver_published(session)
        session.mail_poll_at = now + 15


def inbox(pid):
    now = int(time.time())
    result = {}
    for blob in db.get_mail(pid):
        mail = pb.MailBriefData.FromString(blob)
        if mail.collect_status or not mail.overdue_day or now < mail.send_time + mail.overdue_day * 86400:
            result[mail.mail_id] = mail
    return result


def list_mails(session, req, rsp):
    deliver_published(session, notify=False)
    rsp.mails.extend(inbox(session.player_id).values())
    return pb.StatusCode_OK


def save(pid, mail, rsp):
    db.set_mail(pid, mail.mail_id, mail.SerializeToString())
    rsp.mails.add().CopyFrom(mail)


def templates(pid, item_id, count, receipt, pack):
    row = _table("Item","item").get(item_id)
    if row is None or count <= 0:
        raise UnsupportedGoods
    if row.get("new_bag_item_tag") == pb.EBagItemTag_Pet:
        if item_id not in _table("Pet","pet"):
            raise UnsupportedGoods
        for _ in range(count):
            attachment(pid, make_item(item_id, 1, pid), receipt, pack)
        return
    # Route through the same warehouse inventory grants as other native item delivery.
    award(pid, {item_id: count}, receipt, pack)


def attachment(pid, detail, receipt, pack):
    item = detail.main_item
    kind = item.WhichOneof("item")
    if kind == "base_item":
        if item.base_item.item_id not in (0, item.item_id):
            raise UnsupportedGoods
        templates(pid, item.item_id, item.base_item.num, receipt, pack)
        return
    if kind not in INSTANCE_TYPES or INSTANCE_TYPES[kind] != item.item_tag:
        raise UnsupportedGoods
    cfg = _table("Item","item").get(item.item_id)
    if not cfg or cfg.get("new_bag_item_tag") != item.item_tag:
        raise UnsupportedGoods
    stored = pb.ItemDetail()
    stored.CopyFrom(detail)
    stored.pack_type = pb.ItemDetail.PackType_Inventory
    stored.main_item.temp_pack_index = 0
    instance = getattr(stored.main_item, kind)
    template_field = "item_id" if kind in ("treasure", "pet") else kind + "_id"
    if getattr(instance, template_field) != item.item_id:
        raise UnsupportedGoods
    if kind == "pet" and item.item_id not in _table("Pet","pet"):
        raise UnsupportedGoods
    if kind in ("weapon", "armor", "poster"):
        instance.wearer_id = 0
    # Dropped equipment can carry rolled attributes. Preserve them even when its old ID is occupied.
    if not instance.instance_id or db.get_item_detail(pid,instance_id=instance.instance_id):
        instance.instance_id = db.get_instance_id(pid)
        while db.get_item_detail(pid,instance_id=instance.instance_id):
            instance.instance_id = db.get_instance_id(pid)
    db.conn.execute("INSERT INTO instance(player_id,instance_id) VALUES(?,?) ON CONFLICT(player_id) "
                    "DO UPDATE SET instance_id=MAX(instance_id,excluded.instance_id)",(pid,instance.instance_id))
    db.set_item_detail(pid, stored.SerializeToString(), instance_id=instance.instance_id)
    pack.items.add().CopyFrom(stored)
    receipt.items.add().CopyFrom(stored)


def pets_in_mail(mails):
    species = _table("Pet", "pet")
    incoming_pets = sum(item.num for mail in mails for item in mail.items if item.item_id in species)
    for mail in mails:
        for detail in mail.reward:
            item = detail.main_item
            if item.HasField("pet"):
                incoming_pets += 1
            elif item.HasField("base_item") and item.item_id in species:
                incoming_pets += item.base_item.num
    return incoming_pets


def claim(pid, mails, rsp):
    receipt = pb.ShopBuyRsp()
    pack = pb.PackNotice(status=pb.StatusCode_OK)
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")
    db.conn.execute("SAVEPOINT mail_attachments")
    try:
        incoming = pets_in_mail(mails)
        if incoming and not has_room(pid, incoming):
            rsp.status = pb.StatusCode_PET_GRID_FULL
            return []
        for mail in mails:
            for item in mail.items:
                templates(pid, item.item_id, item.num, receipt, pack)
            for detail in mail.reward:
                attachment(pid, detail, receipt, pack)
            mail.mail_state = pb.MailState_CLAIMED
            save(pid, mail, rsp)
        rsp.claim_result.extend(receipt.items)
        exp = sum(r.main_item.base_item.num for r in receipt.items if r.main_item.item_id == 1)
        level = credit_player_experience(pid, exp) if exp else None
    except UnsupportedGoods:
        db.conn.execute("ROLLBACK TO mail_attachments")
        rsp.ClearField("mails")
        rsp.ClearField("claim_result")
        rsp.status = pb.StatusCode_CFG_NOT_FOUND
        return []
    except BaseException:
        db.conn.execute("ROLLBACK TO mail_attachments")
        raise
    finally:
        db.conn.execute("RELEASE mail_attachments")
    notices = [(MsgId.PackNotice, pack)] if pack.items else []
    if level is not None:
        notices.append((MsgId.PlayerLevelExpNotice, level))
    return notices


def operate(session, req, packet_id):
    pid, op = session.player_id, req.operate_type
    rsp = pb.OperateMailsRsp(operate_type=op, status=pb.StatusCode_OK)
    deliver_published(session, notify=False)
    box = inbox(pid)
    mail = box.get(req.mail_id)
    notices = []
    if op in (READ, CLAIM, COLLECT) and mail is None:
        rsp.status = pb.StatusCode_MAIL_NOT_EXIST
    elif op == READ:
        if mail.mail_state == pb.MailState_UNREAD:
            mail.mail_state = pb.MailState_UNCLAIM if mail.items or mail.reward else pb.MailState_READED
        save(pid, mail, rsp)
    elif op == COLLECT:
        cache = getattr(session, "mail_collect_receipts", None)
        key = (packet_id, req.mail_id)
        if packet_id and cache is not None and key in cache:
            rsp.ParseFromString(cache[key])
        else:
            limit = int(next(r for r in res["GlobalConfig"]["config"]["datas"]
                             if r["key"] == "Mail_CollectMax_Count")["values"][0])
            if not mail.collect_status and sum(m.collect_status for m in box.values()) >= limit:
                rsp.status = pb.StatusCode_MAIL_COLLECT_NUM_LIMIT
            else:
                mail.collect_status = rsp.collect_status = not mail.collect_status
                save(pid, mail, rsp)
            if packet_id:
                cache_after_commit(session, "mail_collect_receipts", key, rsp.SerializeToString())
    elif op in (CLAIM, CLAIM_ALL):
        candidates = [m for m in ([mail] if op == CLAIM else box.values())
                      if (m.items or m.reward) and m.mail_state != pb.MailState_CLAIMED]
        if op == CLAIM and not candidates:
            rsp.status = pb.StatusCode_MAIL_NO_REWARD
        else:
            notices = claim(pid, candidates, rsp)
    elif op == DELETE:
        for current in box.values():
            if current.collect_status:
                continue
            if current.mail_state == pb.MailState_CLAIMED or (
                    current.mail_state == pb.MailState_READED and not (current.items or current.reward)):
                db.del_mail(pid, current.mail_id)
                rsp.deleted_mail_ids.append(current.mail_id)
    else:
        rsp.status = pb.StatusCode_BAD_REQ
    session.send(MsgId.OperateMailsRsp, rsp, packet_id)
    for msg, notice in notices:
        session.send(msg, notice, 0)
