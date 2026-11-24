from private_request import handler
from private_sandbag import claim

Handler = handler("ActivitySandbagReward", claim, replay=True)
