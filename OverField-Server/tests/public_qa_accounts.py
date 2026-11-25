"""Normal public account flow for QA. A test runner can supply privileged fixture setup."""
from base64 import b64decode
from contextlib import closing
from getpass import getpass
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def post(channels,action,data):
    with channels.open(18881) as stream, closing(HTTPConnection("localhost",18881,timeout=12)) as conn:
        conn.sock=stream
        conn.request("POST","/local/v1/accounts/"+action,json.dumps(data).encode(),
                     {"Content-Type":"application/json","Connection":"close"})
        reply=conn.getresponse()
        return reply.status,json.loads(reply.read(65536))


def account(channels,action,data,expected=200):
    status,fields=post(channels,action,data)
    if action=="register" and status==428 and fields.get("error")=="captcha_required":
        provision=getattr(channels,"qa_fixture",None)
        if provision is not None:
            # Only the local test owner can seed a fresh QA character. Sign in through public TLS.
            provision(data)
            return account(channels,"login",{k:data[k] for k in ("username","password")},200)
        if not sys.stdin.isatty():
            raise RuntimeError("Public registration requires a human challenge. Run interactively or provision a dedicated test fixture via the realm operator.")
        status,challenge=post(channels,"challenge",{"username":data["username"],"purpose":"register"})
        if status!=200:
            raise RuntimeError(f"Account challenge: HTTP {status}")
        with TemporaryDirectory() as scratch:
            image=Path(scratch)/"captcha.png"
            image.write_bytes(b64decode(challenge["image_png"],validate=True))
            answer=getpass(f"Open {image} and enter the five digits: ")
        status,fields=post(channels,"register",dict(data,answer=answer,challenge_id=challenge["challenge_id"]))
    if status!=expected:
        raise RuntimeError(f"{action}: HTTP {status} {fields.get('error','')}")
    return fields


def local_fixture(channels,authority_port):
    from test_private_profiles import request
    def seed(data):
        if not data["username"].startswith("qa"):raise ValueError("Expected synthetic QA username")
        status,_=request(authority_port,"POST","/local/v1/accounts/register",data)
        if status!=201:raise RuntimeError(f"Local QA account setup: HTTP {status}")
    channels.qa_fixture=seed
    return channels
