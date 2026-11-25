from private_redemption import inherit
from private_request import handler


Handler = handler("InheritCodeUse", inherit, replay=True)
