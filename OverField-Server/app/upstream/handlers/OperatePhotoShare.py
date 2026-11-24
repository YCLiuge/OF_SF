from private_photos import operate_photo
from private_request import handler


Handler = handler("OperatePhotoShare", operate_photo, replay=True)
