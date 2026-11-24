from private_field_activities import encounter
from private_request import handler


Handler = handler("BattleEncounterStateUpdate", encounter, replay=True)
