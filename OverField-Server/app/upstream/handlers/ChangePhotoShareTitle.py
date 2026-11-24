from private_photos import change_title
from private_request import handler


Handler = handler("ChangePhotoShareTitle", change_title, replay=True)
