from private_actions_study import learn
from private_request import handler

Handler = handler("ActionStudy", learn, replay=True)
