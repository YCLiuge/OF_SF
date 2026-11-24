from functools import partial

from private_gomoku import online
from private_request import handler

Handler = handler("GomokuOnlinePlayerStartPlay",partial(online,"start"),replay=True)
