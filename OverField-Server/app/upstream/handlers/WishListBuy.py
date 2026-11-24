from private_request import handler
from private_wishes import buy

Handler = handler("WishListBuy", buy, replay=True)
