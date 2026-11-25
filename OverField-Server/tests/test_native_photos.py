"""Exercise the original client's photo wall protocol, native media URLs and portable storage."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import socket
import struct
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

from game_dispatch import GameDispatch
from local_game_service import LocalGameService, pb
from local_identity import LocalIdentity
import plaza_assets
from migrate import backup, restore
from photo_media import PUBLIC, NATIVE, dimensions, local_photo
from private_profiles import create_profile_server
from private_shop import CN
from realm_maintenance import SelectedFeed
from realm_admin import execute
import realm_plaza
from test_game_dispatch import listening, http
from test_game_realm import login, join_after_intro
from test_native_gacha import call
from utils import db
from utils.pb_create import make_item

# Real 320 x 180 WebP encoded by libwebp.
WEBP = base64.b64decode("UklGRiQAAABXRUJQVlA4TBcAAAAvP8EsAAcQ/Y/+BwAU6f9/iuh/6v8PAAA=")
OK = pb.StatusCode_OK


@contextmanager
def media_servers(identity):
    service = create_profile_server(identity, port=0)
    port = service.server_port
    dispatch = GameDispatch(f"http://127.0.0.1:{port}", "127.0.0.1", 11033)
    dispatch.maintenance = service.maintenance
    service.game_dispatch = dispatch
    thread = Thread(target=service.serve_forever, kwargs={"poll_interval":.03}, daemon=True)
    thread.start()
    class Channels:
        @contextmanager
        def open(self, p):
            assert p == port
            with socket.create_connection(("127.0.0.1",p),timeout=5) as channel:
                yield channel
    try:
        selected = GameDispatch("http://127.0.0.1:18882","127.0.0.1",11033)
        selected.maintenance = SelectedFeed(SimpleNamespace(channels=Channels(),http_port=port))
        with listening(selected) as proxy:
            yield port, proxy
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def run(root, resources):
    state = root/"state"
    state.mkdir()
    identity = LocalIdentity(state/"identity.sqlite3")
    profiles = [identity.create_profile(v) for v in ("摄影甲","摄影乙")]
    game = LocalGameService(identity,state,resources,port=0)
    def connect(idx):
        sock,_,pid = login(game.port,identity.issue_ticket(*profiles[idx]))
        main,_ = join_after_intro(sock)
        return sock,pid,main
    def balance(pid):
        return pb.ItemDetail.FromString(db.get_item_detail(pid,102)).main_item.base_item.num
    def set_balance(pid, amt):
        db.set_item_detail(pid,make_item(102,amt,pid).SerializeToString(),102)
    assert dimensions(WEBP) == (320,180)
    game.start()
    try:
        a,owner,initial = connect(0)
        b,viewer,_ = connect(1)
        with a,b,media_servers(identity) as (local,proxy):
            clothes = 4011023
            dye = pb.PosColor(pos=1,red=251,green=37,blue=98)
            with game.lock,db.conn:
                garment = pb.ItemDetail.FromString(db.get_item_detail(owner,clothes))
                garment.main_item.outfit.dye_schemes[0].colors.add().CopyFrom(dye)
                db.set_item_detail(owner,garment.SerializeToString(),clothes)
                set_balance(owner,50)
                set_balance(viewer,0)
            character = next(c for c in initial.characters if c.character_id==101001)
            preset = pb.OutfitPreset()
            preset.CopyFrom(character.outfit_presets[0])
            preset.clothes=clothes
            assert call(a,"OutfitPresetUpdate",10,char_id=101001,preset=preset).status==OK
            assert call(a,"OutfitPresetSwitch",11,char_id=101001,use_preset_index=0).status==OK
            assert call(a,"UploadPhotoShare",110,file=WEBP,title=" ").status==pb.StatusCode_BAD_REQ
            bad = call(a,"UploadPhotoShare",12,file=WEBP[:-1],title="坏图")
            assert bad.status==pb.StatusCode_BAD_REQ
            upload = dict(file=WEBP,title="花园",tag1="风景",camera_param=pb.CameraParameters(camera_version=3))
            first = call(a,"UploadPhotoShare",13,**upload)
            assert first.status==OK,first
            code = first.info.code
            assert first.info.camera_param.camera_version==3 and first.info.character_id==101001
            assert 1_500_000_000 < first.info.upload_time < 4_000_000_000
            assert first.info.player_id==owner and code>0
            source = next(c for c in first.info.outfits if c.outfit_id==clothes)
            assert list(source.dye_schemes.colors)==[dye]
            assert call(a,"UploadPhotoShare",13,**upload)==first
            assert call(a,"UploadPhotoShare",14,**upload)==first
            latest = call(b,"PhotoShareSearch",20,search_type=pb.PhotoSearchType_NEW)
            assert latest.status==OK and latest.total_num==1 and latest.photos[0].code==code
            assert latest.today_upload_limit==20 and latest.upload_max_num==10
            assert call(b,"PhotoShareSearch",21,search_type=pb.PhotoSearchType_MY_SHARE).total_num==0
            for sub,text in ((pb.PhotoSearchTextType_UID,str(owner)),(pb.PhotoSearchTextType_CODE,str(code)),
                             (pb.PhotoSearchTextType_TEXT,"风景")):
                r=call(b,"PhotoShareSearch",22+sub,search_type=pb.PhotoSearchType_SEARCH,text_type=sub,text=text)
                assert [p.code for p in r.photos]==[code]
            for port,path in ((local,PUBLIC),(local,NATIVE),(proxy,NATIVE)):
                for part in ("","thumb/"):
                    url=path+part+str(code)
                    status,headers,body=http(port,"GET",url)
                    assert (status,headers["Content-Type"],body)==(200,"image/webp",WEBP)
                    head_status, head_headers, head_body = http(port,"HEAD",url)
                    assert (head_status,head_headers["Content-Length"],head_body)==(200,str(len(WEBP)),b"")
                assert http(port,"GET",path+"9999999999")[0]==404
                assert http(port,"GET",path+"../game.sqlite")[0]==404
                assert http(port,"GET",url,headers={"Authorization":"example"})[0]==403
            assert http(proxy,"GET",PUBLIC+str(code))[0]==404
            def action(sock,seq,op):
                return call(sock,"OperatePhotoShare",seq,code=code,operate_type=op)
            assert action(b,30,pb.PhotoOperateType_DEL).status!=OK
            assert action(b,31,pb.PhotoOperateType_LIKE).status==OK
            assert action(b,32,pb.PhotoOperateType_LIKE).status==OK
            assert action(b,33,pb.PhotoOperateType_FAVORITE).status==OK
            assert call(b,"PhotoShareSearch",34,search_type=pb.PhotoSearchType_MY_FAVORITE).total_num==1
            observed=call(a,"PhotoShareDetail",35,code=code).info
            assert observed.likes_total==observed.favorite_total==1 and not observed.likes_status
            assert call(b,"PhotoShareDetail",36,code=code).info.likes_status
            assert call(b,"ChangePhotoShareTitle",37,code=code,title="代改").status!=OK
            assert call(a,"ChangePhotoShareTitle",38,code=code,title="新标题",tag1="风景").status==OK
            assert call(a,"ChangePhotoShareTitle",39,code=code,title="新标题",tag1="风景").status==OK
            assert call(a,"ChangePhotoShareTitle",40,code=code,title="再改").status==pb.StatusCode_ITEM_NOT_ENOUGH
            assert call(b,"PhotoShareDetail",41,code=code).info.title=="新标题"
            assert call(b,"PhotoShareCopyFashionDye",42,code=code,outfit=source).status==pb.StatusCode_ITEM_NOT_ENOUGH
            with game.lock,db.conn:
                assert balance(owner)==0
                set_balance(viewer,50)
            forged=pb.PhotoShareOutfit()
            forged.CopyFrom(source)
            forged.dye_schemes.colors[0].red=123
            assert call(b,"PhotoShareCopyFashionDye",43,code=code,outfit=forged).status!=OK
            saved=call(b,"PhotoShareCopyFashionDye",44,code=code,outfit=source)
            assert saved.status==OK and call(b,"PhotoShareCopyFashionDye",44,code=code,outfit=source)==saved
            assert call(b,"PhotoShareCopyFashionDye",45,code=code,outfit=source).status==OK
            assert call(a,"PhotoShareDetail",46,code=code).info.dye_download_total==1
            with game.lock:
                assert balance(viewer)==0
                assert list(pb.ItemDetail.FromString(db.get_item_detail(viewer,clothes)).main_item.outfit.dye_schemes[0].colors)==[dye]
            add = call(a,"PhotoSharePaymentAddUploadGrid",47)
            assert add.status==OK and call(a,"PhotoSharePaymentAddUploadGrid",47)==add
            assert call(a,"PhotoShareSearch",48,search_type=pb.PhotoSearchType_MY_SHARE).upload_max_num==15
            with patch("private_photos.now",return_value=datetime(2026,11,1,12,tzinfo=CN)):
                fields=dict(upload,is_activity=True)
                event=call(a,"UploadPhotoShare",51,**fields)
                assert event.status==OK and event.info.activity_id==260815120,event
                other=call(a,"UploadPhotoShare",52,**dict(fields,title="活动二"))
                assert other.status==OK
                my=call(a,"PhotoShareSearch",152,search_type=pb.PhotoSearchType_MY_SHARE,end_index=8)
                assert {p.code for p in my.activity_photos}=={event.info.code,other.info.code}
                assert [p.code for p in my.photos]==[code]
                assert call(a,"UploadPhotoShare",53,**dict(fields,title="活动三")).status==pb.StatusCode_PS_ACTIVITY_UPLOAD_LIMIT
                assert call(b,"PhotoShareSearch",54,search_type=pb.PhotoSearchType_Activity_Rank).total_num==2
                assert call(a,"OperatePhotoShare",55,code=event.info.code,activity_id=260815120,
                            operate_type=pb.PhotoOperateType_MOVE).status==OK
                assert call(b,"PhotoShareSearch",56,search_type=pb.PhotoSearchType_Activity_Rank).total_num==1
                assert call(a,"OperatePhotoShare",57,code=event.info.code,operate_type=pb.PhotoOperateType_MOVE).status!=OK
            with patch("private_photos.now",return_value=datetime(2026,1,18,12,tzinfo=CN)):
                assert call(a,"UploadPhotoShare",58,**dict(upload,is_activity=True)).status==pb.StatusCode_PS_ACTIVITY_UPLOAD_END
            plan=dict(theme=3,activities=[8011],arcade=0,opens_at="2027-03-01 00:00:00",closes_at="2027-04-01 00:00:00")
            assert realm_plaza.photo_deadline(8011,plan["opens_at"],plan["closes_at"])=="2027-03-16 23:59:59"
            import json
            with game.lock,db.conn:
                db.conn.execute("INSERT OR REPLACE INTO authority.private_event_config(id,settings) VALUES(1,?)",
                                (json.dumps(dict(events=[],plaza=realm_plaza.validate(plan))),))
            with patch("private_photos.now",return_value=datetime(2027,3,10,12,tzinfo=CN)):
                response=call(a,"UploadPhotoShare",158,**dict(upload,is_activity=True))
                assert response.status==OK and response.info.activity_id==8011
            with patch("private_photos.now",return_value=datetime(2027,3,18,12,tzinfo=CN)):
                assert call(a,"UploadPhotoShare",159,**dict(upload,is_activity=True)).status==pb.StatusCode_PS_ACTIVITY_UPLOAD_END
            listed=execute(state,"photos.list",{"limit":1},None)["photos"]
            assert len(listed)==1 and listed[0]["code"]==response.info.code
            assert execute(state,"photos.list",{"before":response.info.code,"limit":1},None)["photos"][0]["code"]<response.info.code
            assert execute(state,"photos.withdraw",{"code":response.info.code},None)=={"withdrawn":response.info.code}
            assert http(proxy,"GET",NATIVE+str(response.info.code))[0]==404
            assert call(b,"PhotoShareDetail",160,code=response.info.code).status==pb.StatusCode_PS_NOT_EXIST
            assert action(b,60,pb.PhotoOperateType_FAVORITE_CANCEL).status==OK
            assert action(a,61,pb.PhotoOperateType_DEL).status==OK
            assert http(proxy,"GET",NATIVE+str(code))[0]==404
            assert call(b,"PhotoShareDetail",62,code=code).status==pb.StatusCode_PS_NOT_EXIST
            with patch("private_photos.now",return_value=datetime(2027,4,1,12,tzinfo=CN)):
                assert call(a,"UploadPhotoShare",63,**dict(upload,is_activity=True)).status==pb.StatusCode_PS_ACTIVITY_NOT_OPEN
                with game.lock,db.conn:
                    db.conn.execute("UPDATE realm_photo_quotas SET day='2027-04-01', used=20 WHERE player=?",(owner,))
                assert call(a,"UploadPhotoShare",64,**dict(upload,title="用尽名额")).status==pb.StatusCode_PS_TODAY_UPLOAD_LIMIT
            with patch("private_photos.now",return_value=datetime(2027,4,2,5,tzinfo=CN)):
                assert call(a,"UploadPhotoShare",65,**dict(upload,title="新的一天")).status==OK
    finally:
        game.stop()
    packed=root/"backup.zip"
    backup(state,packed)
    restored=root/"restored"
    restore(packed,restored)
    assert local_photo(restored,event.info.code)==WEBP
    print("PASS: seven native photo requests, scoped search, media routes on local/selected realms, color copy, quotas, approval boundaries and backup/restore.")


def verify_mounted_calendar(client):
    from game_dispatch import Assets,PATCH_BUNDLES
    assets=Assets(client)
    stem=PATCH_BUNDLES[assets.patch]
    opens, closes = "2027-03-01 00:00:00", "2027-04-01 00:00:00"
    with assets.open_stock(stem+".bundle") as stock:
        payload,_ = plaza_assets.updated_bundle(stock.read(),dict(theme=3,activities=[8011],arcade=0,opens_at=opens,closes_at=closes))
    header = payload.index(b"\0",payload.index(b"\0",12)+1)+1
    _,info_size,_,flags = struct.unpack_from(">QIII",payload,header)
    assert flags & 63 == 0
    info_start=(header+20+15)&~15
    block_count, = struct.unpack_from(">I",payload,info_start+16)
    cursor=(info_start+info_size+15)&~15
    node=bytearray()
    for i in range(block_count):
        size,packed,mode=struct.unpack_from(">IIH",payload,info_start+20+i*10)
        source=payload[cursor:cursor+packed]
        node.extend(plaza_assets._lz4(source,size) if mode else source)
        cursor+=packed
    def row(sheet,chosen):
        off,size=plaza_assets.KNOWN_BUNDLES[stem][0][sheet]
        for group,start,end in plaza_assets._fields(node,off,off+size):
            if group==1:
                for _,a,b in plaza_assets._fields(node,start,end):
                    fields={f:node[v:last].decode("ascii") if last and last-v==19 else v
                            for f,v,last in plaza_assets._fields(node,a,b)}
                    if fields[1]==chosen:
                        return fields
        raise AssertionError(chosen)
    assert row("Activity",8011)[3]==opens and row("Activity",8011)[4]==closes
    assert row("Activity",8012)[3]==plaza_assets.OFF_START
    assert row("ActivityFashionShow",8011)[2]=="2027-03-16 23:59:59"
    print("PASS: selected competition and submission deadline decoded from the rebuilt national Unity bundle.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--resources",type=Path,required=True)
    parser.add_argument("--client-root",type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:
        run(Path(tmp),args.resources.resolve())
    if args.client_root:
        verify_mounted_calendar(args.client_root.resolve())
