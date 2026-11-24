from private_photos import upload_photo
from private_request import handler


Handler = handler("UploadPhotoShare", upload_photo, replay=True)
