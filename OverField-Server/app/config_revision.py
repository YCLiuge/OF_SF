"""Conditional writes for independently managed realm settings."""
from hashlib import sha256
import json


class ConfigChanged(ValueError):
    pass


def revision(value):
    wire = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return '"' + sha256(wire).hexdigest() + '"'


def check(value, expected):
    if expected is not None and revision(value) != expected:
        raise ConfigChanged("配置已被其他操作者更改。请重新读取区服配置，再合并本次修改。")
