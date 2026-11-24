from functools import partial

from private_gomoku import online
from private_request import handler

Handler = handler("GomokuOnlinePlayerLeave",partial(online,"leave"),replay=True)
