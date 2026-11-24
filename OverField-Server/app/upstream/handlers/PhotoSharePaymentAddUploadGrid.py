from private_photos import expand_gallery
from private_request import handler


Handler = handler("PhotoSharePaymentAddUploadGrid", expand_gallery, replay=True)
