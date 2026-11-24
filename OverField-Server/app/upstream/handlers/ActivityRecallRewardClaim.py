from private_request import handler
from private_referrals import recall_claim

Handler = handler("ActivityRecallRewardClaim", recall_claim, replay=True)
