from private_character_life import dead
from private_request import handler

Handler = handler("CharacterDead", dead, replay=True)
