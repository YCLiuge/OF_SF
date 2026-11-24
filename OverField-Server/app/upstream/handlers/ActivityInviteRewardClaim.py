from private_request import handler
from private_referrals import invite_claim

Handler = handler("ActivityInviteRewardClaim", invite_claim, replay=True)
