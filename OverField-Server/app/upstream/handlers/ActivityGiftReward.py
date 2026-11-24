from private_activity_gift import claim
from private_request import handler

Handler = handler("ActivityGiftReward", claim, replay=True)
