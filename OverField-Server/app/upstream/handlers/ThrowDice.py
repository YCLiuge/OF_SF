from private_request import handler
from private_room_actions import dice

Handler = handler("ThrowDice", dice, replay=True)
