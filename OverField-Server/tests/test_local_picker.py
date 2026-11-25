"""Verify the optional native server-list editor and the loopback-only GUI trigger."""
import json
import os
from pathlib import Path
import socket
import time
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
from urllib.parse import urlsplit

from game_dispatch import GameDispatch
from local_picker import LABEL, LocalPicker
from native_account_seats import NativeAccountSeats
from remote_password import RemoteLogin
from ssh_game_channel import valid_target
from test_game_dispatch import http, listening


class EditorTests(TestCase):
    def test_only_disconnected_authenticated_process_can_resume_within_grace(self):
        client=Path("launcher.exe")
        seats=NativeAccountSeats(None,client)
        first=RemoteLogin("one","ticket-1",resume_token="secret-1")
        second=RemoteLogin("one","ticket-2",resume_token="secret-2")
        calls=[]
        seats.remote=SimpleNamespace(resume=lambda grant:(calls.append(grant) or second))
        seats.pending[21]=(73,first,300)
        owner=[21,73]
        with patch("native_account_seats.connection_pid",side_effect=lambda _:owner[0]), \
             patch("native_account_seats.process_info",side_effect=lambda _:(client,7,owner[1])), \
             patch("native_account_seats.time.monotonic",return_value=100) as clock:
            grant=seats.claim(None)
            with self.assertRaises(ValueError):seats.claim(None)
            grant.on_disconnect()
            for foreign in ([22,73],[21,74]):
                owner[:]=foreign
                with self.assertRaises(ValueError):seats.claim(None)
            self.assertFalse(calls)
            owner[:]=[21,73]
            renewed=seats.claim(None)
            self.assertEqual(renewed.ticket,"ticket-2")
            self.assertEqual(calls,[first])
            with self.assertRaises(ValueError):seats.claim(None)
            renewed.on_disconnect()
            clock.return_value=221
            with self.assertRaises(ValueError):seats.claim(None)
            self.assertEqual(calls,[first])

    def test_disconnected_player_is_offered_password_login_once(self):
        client=Path("launcher.exe")
        seats=NativeAccountSeats(None,client)
        seats.picker=LocalPicker("test-patch.exe")
        seats.remote=object()
        seats.pending[21]=(73,RemoteLogin("private-slot","one-use-ticket"),time.monotonic()+300)
        owner=[21,73]
        foreground=[False]
        with patch("native_account_seats.connection_pid",side_effect=lambda _:owner[0]), \
             patch("native_account_seats.process_info",side_effect=lambda _: (client,7,owner[1])), \
             patch.object(seats.picker,"player_is_foreground",side_effect=lambda _:foreground[0]), \
             patch("local_picker.subprocess.Popen") as opener:
            grant=seats.claim(None)
            self.assertEqual(grant.ticket,"one-use-ticket")
            with self.assertRaises(ValueError):
                seats.claim(None)
            opener.assert_not_called()
            grant.on_disconnect()
            for candidate in ([22,73],[21,74],[21,73]):
                owner[:]=candidate
                with self.assertRaises(ValueError):
                    seats.claim(None)
            opener.assert_not_called()
            foreground[0]=True
            for _ in range(3):
                with self.assertRaises(ValueError):
                    seats.claim(None)
            opener.assert_called_once()
            self.assertEqual(opener.call_args.args[0],["test-patch.exe","--reconnect"])

    def test_native_list_and_activation(self):
        realm=GameDispatch("http://127.0.0.1:18881","127.0.0.1",11033)
        p=LocalPicker("No-real-UI.exe")
        with listening(realm) as port, patch("local_picker.subprocess.Popen") as run:
            addr="/dispatch/get_login_url_list"
            initial=json.loads(http(port,"POST",addr)[2])["LoginUrlList"]
            self.assertEqual(len(initial),1)
            realm.picker=p
            rows=json.loads(http(port,"POST",addr)[2])["LoginUrlList"]
            self.assertEqual(rows[0],initial[0])
            self.assertEqual(rows[1]["name"],LABEL)
            endpoint=urlsplit(rows[1]["loginUrl"])
            self.assertEqual(rows[1]["loginUrl"], realm.origin + "/settings")
            self.assertEqual(endpoint.hostname,"127.0.0.1")
            chosen=endpoint.path+"/dispatch/client_hot_update"
            realm.assets=object()
            run.return_value.poll.return_value=None
            for headers in ({"Origin":"https://example.org"},{"Sec-Fetch-Mode":"cors"},
                            {"Cookie":"qa=1"},{"Authorization":"fixture"},{"X-Forwarded-For":"192.0.2.1"},
                            {"Referer":"http://127.0.0.1/"},{"X-Real-IP":"192.0.2.1"}):
                self.assertIn(http(port,"POST",chosen,headers=headers)[0], (400,403))
            self.assertEqual(http(port,"GET",chosen)[0],405)
            self.assertEqual(http(port,"POST","/choose-wrong/dispatch/region_info")[0],404)
            self.assertEqual(http(port,"POST",endpoint.path+"/dispatch/get_notice_list")[0],200)
            run.assert_not_called()
            with patch.object(p,"player_is_foreground",return_value=False):
                self.assertTrue(json.loads(http(port,"POST",chosen)[2])["status"])
                run.assert_not_called()
            p.player_is_foreground=lambda connection:True
            for suffix in ("region_info","client_hot_update"):
                code,_,raw=http(port,"POST",endpoint.path+"/dispatch/"+suffix)
                result=json.loads(raw)
                self.assertEqual(code,200)
                self.assertTrue(result["status"])
                if suffix=="region_info":self.assertEqual(result["gate_tcp_ip"],"127.0.0.1")
                else:self.assertEqual(result["hotOssUrl"],realm.origin+"/Resources")
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0],["No-real-UI.exe","--realm-picker"])
            self.assertTrue(json.loads(http(port,"POST","/dispatch/region_info")[2])["status"])
            other=json.loads(http(port,"POST",addr,headers={"X-Forwarded-For":"192.0.2.1"})[2])
            self.assertEqual(other["LoginUrlList"],initial)

    def test_preselected_private_client_skips_repeated_url_menu(self):
        realm = GameDispatch("http://127.0.0.1:18881","127.0.0.1",11033)
        realm.picker = LocalPicker("launcher-fixture.exe", preselected=True)
        realm.assets = object()
        with listening(realm) as port:
            for prefix in ("", "/private-game"):
                route = prefix + "/dispatch/"
                self.assertEqual(json.loads(http(port, "POST", route+"get_login_url_list")[2]), {"LoginUrlList":[]})
                self.assertEqual(json.loads(http(port, "POST", route+"client_hot_update")[2])["hotOssUrl"],
                                 realm.origin + "/Resources")
                self.assertEqual(json.loads(http(port, "POST", route+"region_info")[2])["gate_tcp_port"], 11033)
                external = http(port, "POST", route+"get_login_url_list", headers={"X-Forwarded-For":"192.0.2.1"})
                self.assertEqual(len(json.loads(external[2])["LoginUrlList"]), 1)

    def test_remote_peer_or_unsafe_listener_cannot_trigger(self):
        picker=LocalPicker("No-real-UI.exe")
        for client,bind,allowed in (("127.0.0.1","127.0.0.1",True),("::1","::1",True),
                                     ("192.168.1.55","127.0.0.1",False),("127.0.0.1","0.0.0.0",False),
                                     ("::1","::",False),("203.0.113.8","0.0.0.0",False)):
            handler=SimpleNamespace(client_address=(client,1),server=SimpleNamespace(server_address=(bind,2)),headers={})
            self.assertEqual(picker.local_client(handler),allowed)

    @unittest.skipUnless(os.name == "nt", "Windows tracks the native TCP client PID")
    def test_only_the_tracked_player_can_open_settings(self):
        with TemporaryDirectory() as directory, socket.socket() as listener:
            root = Path(directory)
            marker = root / ".launcher.pid"
            picker = LocalPicker(root / "launcher-fixture.exe")
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with socket.create_connection(listener.getsockname()) as client, listener.accept()[0] as connection:
                self.assertIsNone(picker.requesting_player_pid(connection))
                marker.write_text(str(os.getpid()))
                self.assertEqual(picker.requesting_player_pid(connection), os.getpid())
                marker.write_text(str(os.getpid() + 1))
                self.assertIsNone(picker.requesting_player_pid(connection))

    def test_ssh_target(self):
        for target in ("user@2001:db8::23","::1","alias-name","user@192.0.2.8"):
            self.assertTrue(valid_target(target),target)
        for target in ("-evil","a@@::1"," 127.0.0.1","u@foo:3434","u@fe80::2%foo","::1/path"):
            self.assertFalse(valid_target(target),target)


if __name__=="__main__":
    main()
