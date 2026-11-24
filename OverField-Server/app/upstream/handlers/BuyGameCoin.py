from private_arcade_wallet import buy
from private_request import handler


Handler = handler("BuyGameCoin", buy, replay=True)
