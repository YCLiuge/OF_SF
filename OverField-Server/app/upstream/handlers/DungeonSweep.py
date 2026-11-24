from private_dungeon_sweep import sweep
from private_request import handler


Handler = handler("DungeonSweep", sweep, replay=True)
