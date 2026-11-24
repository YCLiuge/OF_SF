from private_request import handler
from private_room_actions import clap

Handler = handler("Clap", clap, replay=True)
