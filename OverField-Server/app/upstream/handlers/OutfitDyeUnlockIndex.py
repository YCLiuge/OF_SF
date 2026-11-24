from private_outfit_dye import unlock_scheme
from private_request import handler


Handler = handler("OutfitDyeUnlockIndex", unlock_scheme, replay=True)
