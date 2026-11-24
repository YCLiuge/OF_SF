from private_request import handler
from private_wishes import operate

Handler = handler("WishListOperate", operate, replay=True)
