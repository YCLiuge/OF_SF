from private_abyss import operate
from private_request import handler


Handler = handler("DungeonOperate", operate, replay=True)
