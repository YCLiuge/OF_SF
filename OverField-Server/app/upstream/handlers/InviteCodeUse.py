from private_request import handler
from private_referrals import invite_use

Handler = handler("InviteCodeUse", invite_use, replay=True)
