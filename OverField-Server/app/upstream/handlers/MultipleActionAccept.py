from private_request import handler
from private_room_actions import accept

Handler = handler("MultipleActionAccept", accept, replay=True)
