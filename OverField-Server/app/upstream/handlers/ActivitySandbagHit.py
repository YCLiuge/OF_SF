from private_request import handler
from private_sandbag import hit

Handler = handler("ActivitySandbagHit", hit, replay=True)
