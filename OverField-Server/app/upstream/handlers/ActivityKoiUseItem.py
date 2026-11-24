from private_koi import use
from private_request import handler

Handler = handler("ActivityKoiUseItem", use, replay=True)
