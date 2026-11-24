from private_arcade_wallet import exchange
from private_request import handler


Handler = handler("DailyTaskExchange", exchange, replay=True)
