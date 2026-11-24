from private_request import handler
from private_inventory import page

Handler = handler("GetPet", lambda session, req, rsp: page(session, req, rsp, "pet"))
