"""Ephemeral, realm-issued challenges for the public player account endpoints."""
from collections import OrderedDict, deque
import base64
import hashlib
import hmac
import secrets
import struct
from threading import Lock
from time import monotonic
import zlib

from realm_accounts import AccountError, USER

DIGITS = (
    ("01110","10001","00001","00010","00100","01000","11111"),
    ("11110","00001","00001","01110","00001","00001","11110"),
    ("00010","00110","01010","10010","11111","00010","00010"),
    ("11111","10000","10000","11110","00001","00001","11110"),
    ("01110","10000","10000","11110","10001","10001","01110"),
    ("11111","00001","00010","00100","01000","01000","01000"),
    ("01110","10001","10001","01110","10001","10001","01110"),
    ("01110","10001","10001","01111","00001","00001","01110"),
)
ALPHABET="23456789"
TTL=120


def picture(answer):
    width,height=164,52
    rng=secrets.SystemRandom()
    pixels=bytearray([248])*(width*height)
    for _ in range(260):
        pixels[rng.randrange(len(pixels))]=rng.randrange(140,216)
    for digit,char in enumerate(answer):
        glyph=DIGITS[ord(char)-ord("2")]
        left=8+digit*30+rng.randint(-2,2)
        top=rng.randint(6,12)
        shade=rng.randint(15,65)
        slant=rng.choice((-1,0,1))
        for y,row in enumerate(glyph):
            for x,bit in enumerate(row):
                if bit!="1":continue
                for dy in range(5):
                    start=(top+y*5+dy)*width+left+x*4+(y-3)*slant
                    pixels[start:start+4]=bytes([shade])*4
    def chunk(tag,data):
        return struct.pack(">I",len(data))+tag+data+struct.pack(">I",zlib.crc32(tag+data)&0xffffffff)
    raw=b"".join(b"\0"+pixels[y*width:(y+1)*width] for y in range(height))
    png=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,0,0,0,0))
    return base64.b64encode(png+chunk(b"IDAT",zlib.compress(raw,3))+chunk(b"IEND",b"")).decode("ascii")


def username(body):
    user=body.get("username")
    if not isinstance(user,str) or not USER.fullmatch(user):
        raise AccountError(400,"invalid_username")
    return user.lower()


class PlayerCaptcha:
    def __init__(self):
        self.lock=Lock()
        self.challenges=OrderedDict()
        self.proofs=OrderedDict()
        self.rates=OrderedDict()
        self.failures=OrderedDict()

    @staticmethod
    def expire(store,now):
        while store and next(iter(store.values()))[0]<=now:
            store.popitem(last=False)

    def limit(self,source,user,*,shared=False):
        buckets=(("site",240),(("source",source),120 if shared else 20),(("username",user),12),(("pair",source,user),6))
        now=monotonic()
        self.expire(self.rates,now)
        hits=[]
        for key,cap in buckets:
            history=self.rates.get(key,(0,deque()))[1]
            while history and history[0]<=now-60:history.popleft()
            if len(history)>=cap:raise AccountError(429,"rate_limited")
            hits.append((key,history))
        for key,history in hits:
            history.append(now)
            self.rates[key]=(now+60,history)
            self.rates.move_to_end(key)

    def issue(self,body,source,*,shared=False):
        user=username(body)
        purpose=body.get("purpose")
        if set(body)!={"purpose","username"} or purpose not in ("lookup","register","login"):
            raise AccountError(400,"invalid_request")
        with self.lock:
            now=monotonic()
            self.limit(source,user,shared=shared)
            self.expire(self.challenges,now)
            if len(self.challenges)>=2048:raise AccountError(429,"rate_limited")
            token=secrets.token_urlsafe(24)
            answer="".join(secrets.choice(ALPHABET) for _ in range(5))
            self.challenges[token]=(now+TTL,source,user,purpose,hashlib.sha256(answer.encode()).digest())
        return {"challenge_id":token,"image_png":picture(answer),"expires_in":TTL}

    def verify(self,body,user,source,purpose,required):
        """Remove transport fields before passing the request to the existing account API."""
        token=body.pop("challenge_id",None)
        answer=body.pop("answer",None)
        proof=body.pop("account_proof",None)
        if token is None and answer is None and proof is None:
            if required:raise AccountError(428,"captcha_required")
            return
        with self.lock:
            now=monotonic()
            if proof is not None:
                self.expire(self.proofs,now)
                if not isinstance(proof,str) or token is not None or answer is not None or purpose=="lookup":
                    raise AccountError(400,"captcha_invalid")
                grant=self.proofs.get(proof)
                if not grant or grant[1:]!=(source,user,purpose):raise AccountError(400,"captcha_invalid")
                del self.proofs[proof]
                return
            self.expire(self.challenges,now)
            challenge=self.challenges.get(token) if isinstance(token,str) else None
            if not challenge or challenge[1:4]!=(source,user,purpose):raise AccountError(400,"captcha_invalid")
            del self.challenges[token]
            if not isinstance(answer,str) or len(answer)!=5 or not answer.isascii() or not hmac.compare_digest(
                    challenge[4],hashlib.sha256(answer.encode()).digest()):
                raise AccountError(400,"captcha_invalid")

    def lookup_proof(self,source,user,exists):
        with self.lock:
            now=monotonic();self.expire(self.proofs,now)
            if len(self.proofs)>=2048:raise AccountError(429,"rate_limited")
            token=secrets.token_urlsafe(32)
            self.proofs[token]=(now+TTL,source,user,"login" if exists else "register")
        return {"exists":exists,"account_proof":token,"expires_in":TTL}

    def login_challenge(self,source,user,*,shared=False):
        with self.lock:
            self.expire(self.failures,monotonic())
            return any(self.failures.get(key,(0,0))[1]>=cap for key,cap in
                       ((("pair",source,user),3),(("user",user),8),(("source",source),120 if shared else 16)))

    def failed(self,source,user):
        with self.lock:
            now=monotonic();self.expire(self.failures,now)
            for key in (("pair",source,user),("user",user),("source",source)):
                if key not in self.failures and len(self.failures)>=8192:self.failures.popitem(last=False)
                self.failures[key]=(now+300,min(120,self.failures.get(key,(0,0))[1]+1))
                self.failures.move_to_end(key)

    def succeeded(self,source,user):
        with self.lock:
            self.failures.pop(("pair",source,user),None)
            self.failures.pop(("user",user),None)
