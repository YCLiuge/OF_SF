from private_field_activities import voice
from private_request import handler


Handler = handler("CollectVoiceRegion", voice, replay=True)
