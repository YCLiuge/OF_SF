from private_social_profile import birthday
from private_request import handler


Handler = handler("ChangeIsHideBirthday", birthday, replay=True)
