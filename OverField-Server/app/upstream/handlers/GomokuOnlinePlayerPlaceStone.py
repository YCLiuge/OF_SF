from functools import partial

from private_gomoku import online
from private_request import handler

Handler = handler("GomokuOnlinePlayerPlaceStone",partial(online,"place"),replay=True)
