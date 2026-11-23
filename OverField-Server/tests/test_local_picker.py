"""Verify the optional native server-list editor and the loopback-only GUI trigger."""
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch
from urllib.parse import urlsplit

from game_dispatch import GameDispatch
from local_picker import LABEL, LocalPicker
from ssh_game_channel import valid_target
from test_game_dispatch import http, listening


class EditorTests(TestCase):
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

