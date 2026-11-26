"""Only explicit game chat is eligible to leave a player's local endpoint."""
import hashlib
import re
import unicodedata

SECRET=re.compile(r"(?:\b(?:ofk_|oft_|ofp_)[A-Za-z0-9_-]{12,}|\bBearer\s+\S+|(?:login[_-]?token|sdk[_-]?uid|access[_-]?token|authorization|profile[_-]?key)\s*[:=])",re.IGNORECASE)


def chat_text(value):
    if not isinstance(value,str) or len(value)>180:
        return None
    value=unicodedata.normalize("NFC",value).strip()
    if len(value.encode("utf-16-le"))//2>180 or SECRET.search(value):
        return None
    if any(unicodedata.category(c).startswith("C") and c!="\u200d" for c in value):
        return None
    # Unity's TextMeshPro parses angle brackets as rich text.
    return value.replace("<","＜").replace(">","＞")


# The PC profile editor supports this TextMeshPro color form; chat keeps its existing rules.
PROFILE_COLOR = re.compile(r"<(?:color=)?#[0-9a-f]{6}(?:[0-9a-f]{2})?>|</color>",re.I)


def profile_signature(value,limit=60,guard=None):
    if not isinstance(value,str) or len(value)>180:
        return None
    value=unicodedata.normalize("NFC",value).strip()
    try:
        visible=PROFILE_COLOR.sub("",value)
        if (len(value.encode("utf-16-le"))//2>limit or chat_text(value) is None or chat_text(visible) is None
                or guard is not None and (not guard.allows(value) or not guard.allows(visible))):
            return None
    except UnicodeError:
        return None
    # Escape player-supplied tags exactly as ordinary chat does; restore only recognized colors.
    pieces=[]
    start=0
    for tag in PROFILE_COLOR.finditer(value):
        pieces.extend((value[start:tag.start()].replace("<","＜").replace(">","＞"),tag.group()))
        start=tag.end()
    pieces.append(value[start:].replace("<","＜").replace(">","＞"))
    return "".join(pieces)


class ProtectedChat:
    def __init__(self,*secrets):
        self.digests={(len(s),hashlib.sha256(s.encode()).digest()) for s in secrets if s and len(s)<=180}

    def allows(self,value):
        if len(value)>180:
            return False
        for size,digest in self.digests:
            for offset in range(len(value)-size+1):
                part=value[offset:offset+size]
                if hashlib.sha256(part.encode()).digest()==digest:
                    return False
        return True
