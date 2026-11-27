"""The realm welcome letter is issued once, including to existing characters."""
from datetime import datetime, timezone

from proto import net_pb2 as pb
from utils import db
from utils.res_loader import res
from private_wishes import send_mail


NOTICE = """本服由玩家维护，免费用于技术学习、测试与交流。游戏名称、美术、音乐及其他素材归各权利人所有。本服与发行方无关。
请使用合法取得的客户端和存档。请勿公开发布服务器信息、倒卖账号或收取入服费用。测试期间可能出现玩法差异、故障或进度回退，请保留原始备份。如有问题或权属异议，请联系服主。希望这里能成为大家的新家。

This free, player-run server is for non-commercial learning, testing and exchange. The game name, artwork, music and other assets belong to their respective owners. This server is independent of the publisher.
Use a lawfully obtained game client and save. Please keep the server details private. Do not resell accounts or charge for access. Gameplay may differ, and outages or rollbacks can occur. Keep your original backup. Contact the operator about problems or rights concerns. We hope you feel at home here.

ここはプレイヤーが運営する、技術の学習・検証・交流のための非営利サーバーです。利用はいつでも無料です。ゲーム名、美術、音楽などの権利は各権利者に帰属します。運営はゲームの発売元とは無関係です。
正規に入手したクライアントとセーブデータをお使いください。サーバー情報の一般公開、アカウントの転売、参加費の徴収はご遠慮ください。テスト中は仕様の違い、不具合、進行の巻き戻りが生じる場合があります。元のバックアップを保管してください。不具合や権利に関するご相談は管理者まで。皆さんがここでくつろげますように。"""


def notice_text(state):
    path = state / "welcome-mail.txt"
    return path.read_text(encoding="utf-8-sig").strip() if path.is_file() else NOTICE


def deliver_once(session):
    pid = session.player_id
    inserted = db.conn.execute("""INSERT OR IGNORE INTO player_kv VALUES(?,'mail_delivery','community_notice_v1',1)""",(pid,))
    if not inserted.rowcount:
        return
    # UI tag 1 is the consumables tab. Each template in this tab uses stack balances.
    items = [(item,1_000_000) for item,row in res.index("Item","item").items() if row.get("new_u_i_tag")==1]
    legacy = db.get_mail(pid,1)
    send_mail(pid,"公益公告",session.welcome_text,items,datetime.now(timezone.utc),session,sender="区服公告")
    if legacy:
        old = pb.MailBriefData.FromString(legacy)
        if old.sender=="aac" and old.title=="of-ps" and "https://github.com/byzp/of-ps" in old.content:
            db.del_mail(pid,1)
