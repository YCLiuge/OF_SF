from private_redemption import redeem
from private_request import handler


Handler = handler("UseRedemptionCode", redeem, replay=True)
