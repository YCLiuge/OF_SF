from private_character_life import revive
from private_request import handler

Handler = handler("CharacterRevive", revive, replay=True)
