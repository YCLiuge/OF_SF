from private_abyss import team_update
from private_request import handler


Handler = handler("AbyssTeamUpdate", team_update, replay=True)
