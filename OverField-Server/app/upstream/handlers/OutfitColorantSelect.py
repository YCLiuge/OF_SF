from private_outfit_dye import select_colorant
from private_request import handler


Handler = handler("OutfitColorantSelect", select_colorant, replay=True)
