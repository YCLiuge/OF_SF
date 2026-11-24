from private_request import handler
from private_treasure_dig import dig

Handler = handler("TreasureDig", dig, replay=True)
