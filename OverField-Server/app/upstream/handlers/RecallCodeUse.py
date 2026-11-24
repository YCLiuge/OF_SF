from private_request import handler
from private_referrals import recall_use

Handler = handler("RecallCodeUse", recall_use, replay=True)
