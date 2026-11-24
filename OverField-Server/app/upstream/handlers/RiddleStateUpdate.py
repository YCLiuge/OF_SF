from private_field_activities import riddle
from private_request import handler


Handler = handler("RiddleStateUpdate", riddle, replay=True)
