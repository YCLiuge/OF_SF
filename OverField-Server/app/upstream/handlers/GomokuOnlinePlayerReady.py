from functools import partial

from private_gomoku import online
from private_request import handler

Handler = handler("GomokuOnlinePlayerReady",partial(online,"ready"),replay=True)
