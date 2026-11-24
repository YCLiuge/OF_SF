from private_player_reports import submit
from private_request import handler


Handler = handler("Report", submit, replay=True)
