"""Create an isolated native-client discovery configuration from the matching PC release."""
import argparse
import base64
import bz2
import hashlib
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
def digest(data):
    return hashlib.sha256(data).hexdigest()

def export(source, target):
    source = source.resolve(strict=True)
    target = target.resolve()
    if source == target or source.parent.parent in target.parents or target.is_relative_to(source.parent):
        raise ValueError("请选择官服安装目录之外的输出路径。")
    if target.exists():
        raise FileExistsError("输出文件已存在，请选择新的空位置。")
    delta = json.loads(bz2.decompress((HERE / "dispatch-delta.json.bz2").read_bytes()))
    before = source.read_bytes()
    if len(before) != delta["sourceBytes"] or digest(before) != delta["sourceSha256"]:
        raise ValueError("源配置与本补丁针对的国服 1.1.12 文件不一致。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".overfield-", delete=False) as out:
        temp = Path(out.name)
    try:
        cursor = 0
        with temp.open("wb") as out:
            for change in delta["changes"]:
                location, consume = change["offset"], change["remove"]
                if not cursor <= location <= location + consume <= len(before):
                    raise ValueError("配置增量的文件边界错误。")
                out.write(memoryview(before)[cursor:location])
                out.write(base64.b64decode(change["insert"], validate=True))
                cursor = location + consume
            out.write(memoryview(before)[cursor:])
        with temp.open("rb") as original:
            if temp.stat().st_size != delta["resultBytes"] or hashlib.file_digest(original,"sha256").hexdigest()!=delta["resultSha256"]:
                raise ValueError("生成的客户端配置校验失败。")
        # Publishing through a new-name hardlink is atomic and never replaces an existing file.
        os.link(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    return delta

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="从国服 1.1.12 的 data.unity3d 导出本机派发配置，供独立的原版客户端副本使用。")
    parser.add_argument("source",type=Path,help="原版 launcher_Data/data.unity3d")
    parser.add_argument("output",type=Path,help="独立临时配置路径，文件应尚不存在")
    args=parser.parse_args()
    try:
        metadata=export(args.source,args.output)
    except (OSError, ValueError) as error:
        parser.exit(1,str(error)+"\n")
    print("Unity 资源：",metadata["asset"])
    print("本机派发：",metadata["url"])
    print("SHA-256：",metadata["resultSha256"])
    print("输出：",args.output.resolve())

