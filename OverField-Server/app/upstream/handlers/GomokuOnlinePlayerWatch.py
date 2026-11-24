from functools import partial

from private_gomoku import online
from private_request import handler

Handler = handler("GomokuOnlinePlayerWatch",partial(online,"watch"),replay=True)
