from private_room_decor import set_decor
from private_request import handler


Handler = handler("SetRoomDecor", set_decor, replay=True)
