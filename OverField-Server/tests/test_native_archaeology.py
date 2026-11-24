"""Claim individual exhibits and collection milestones through two isolated retail sessions."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from local_game_service import LocalGameService,pb
from local_identity import LocalIdentity
from test_game_realm import login,join_after_intro
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item


def run(root,resources):
    ident=LocalIdentity(root/"identity.sqlite3")
    first=ident.create_profile("考古甲")
    second=ident.create_profile("考古乙")
    host=LocalGameService(ident,root,resources,port=0)
    def connect(profile):
        sock,_,pid=login(host.port,ident.issue_ticket(*profile))
        join_after_intro(sock)
        return sock,pid
    host.start()
    try:
        s,p=connect(first)
        q,other=connect(second)
        with s,q:
            book={"archaeology_type":pb.EArchaeologyType_Book,"id":1}
            achievement={"archaeology_type":pb.EArchaeologyType_Book,"achieve_id":150000501}
            assert call(s,"TreasureIllustrationReward",13,**book).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert call(s,"GetTreasureAchieveReward",14,**achievement).status==pb.StatusCode_FAIL
            with host.lock,db.conn:
                for item in (5090001,5090002):
                    raw=make_item(item,1,p)
                    db.set_item_detail(p,raw.SerializeToString(),item)
            receipt=call(s,"TreasureIllustrationReward",15,**book)
            assert receipt.status==pb.StatusCode_OK and receipt.rewards
            assert call(s,"TreasureIllustrationReward",15,**book)==receipt
            assert call(s,"TreasureIllustrationReward",16,id=5090001).status==pb.StatusCode_REPEAT_GET_REWARD
            assert call(s,"TreasureIllustrationReward",17,id=22).status==pb.StatusCode_CFG_NOT_FOUND
            assert call(s,"GetTreasureAchieveReward",18,archaeology_type=4,achieve_id=150000501).status==pb.StatusCode_CFG_NOT_FOUND
            achieved=call(s,"GetTreasureAchieveReward",19,**achievement)
            assert achieved.status==pb.StatusCode_OK and achieved.items and list(achieved.rewarded_achieve_ids)==[150000501], achieved
            assert call(q,"GetTreasureAchieveReward",20,**achievement).status==pb.StatusCode_FAIL
            assert call(q,"TreasureIllustrationReward",21,**book).status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert list(call(s,"TreasureIllustrationInfo",22).book_reward)==[1]
        host.stop()
        host.start()
        s,p2=connect(first)
        with s:
            assert p2==p and call(s,"GetTreasureAchieveReward",23,**achievement).status==pb.StatusCode_REPEAT_GET_REWARD
    finally:
        host.stop()
    print("PASS archaeology exhibit ownership, inventory aliases, per-type milestones, replay, isolation and restart.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",required=True,type=Path)
    with TemporaryDirectory() as tmp:
        run(Path(tmp),parser.parse_args().resources)
