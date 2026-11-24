from private_field_gathering import gather
from private_request import handler

Handler = handler("Gather", gather, replay=True)
