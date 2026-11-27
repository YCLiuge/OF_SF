"""Verify the native baseline offline. Requires Python 3.11+ and google.protobuf.

Without arguments check OF_SF against the published profile. --client CHANNEL PATH
also reads one installed retail copy. --build-report accepts inspect_client_build.py
output. --lolo accepts the checkout pinned in native-profile.json.
"""
import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

from google.protobuf.descriptor_pb2 import FileDescriptorProto

from audit_requests import extract_catalog

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "contracts/v1/native-profile.json"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check(actual, expected, label):
    if actual != expected:
        raise ValueError(f"{label} differs from the native baseline")


def server_ids(path):
    declarations = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in declarations.body if isinstance(node, ast.ClassDef) and node.name == "MsgId")
    rows = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            name = node.targets[0].id
            if name in rows:
                raise ValueError(f"duplicate MsgId declaration: {name}")
            rows[name] = ast.literal_eval(node.value)
    check(rows.pop("InValid"), -1, "invalid sentinel")
    return rows


def registry_bytes(ids):
    if len(set(ids.values())) != len(ids) or any(value <= 0 for value in ids.values()):
        raise ValueError("duplicate or nonpositive native message ID")
    return "".join(f"{name},{value}\n" for name, value in sorted(ids.items(), key=lambda pair: pair[1])).encode("ascii")


def load_descriptor(path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    blobs = [node.args[0].value for node in ast.walk(module)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "AddSerializedFile" and isinstance(node.args[0], ast.Constant)]
    if len(blobs) != 1:
        raise ValueError("expected one protobuf descriptor")
    return FileDescriptorProto.FromString(blobs[0]), digest(blobs[0])


def check_server(profile, ids, proto):
    check(digest(registry_bytes(ids)), profile["registry"]["sha256"], "message registry")
    declared = {message.name for message in proto.message_type}
    counts = {suffix: sum(name.endswith(suffix) for name in ids) for suffix in ("Req", "Rsp", "Notice")}
    check(counts, profile["registry"]["counts"], "message counts")
    for route in profile["route_examples"]:
        messages = [route["request"], *route.get("conditional_notices", [])]
        if route["reply"] is not None:
            messages.append(route["reply"])
        for message in messages:
            name = message["name"]
            if name not in declared:
                raise ValueError(f"missing protobuf message: {name}")
            check(ids[name], message["id"], name)
        if not (ROOT / route["server_test"]).is_file():
            raise ValueError(f"missing test for {route['request']['name']}")
    enum = next(value for value in proto.enum_type if value.name == "MPPlayRoomEventType")
    check({item.name: item.number for item in enum.value}, profile["mp_room_event_enum"], enum.name)


def build_sample(row):
    def manifest(part):
        return {key: part[key] for key in ("version", "sha256", "bundles")}
    return {
        "backend": row["backend"], "unity": row["unity"],
        "metadata_header_version": row["metadata_header_version"],
        "builtin": manifest(row["builtin"]), "effective": manifest(row["cache"] or row["builtin"]),
        "effective_source": row["cache_source"],
    }


def check_client(path, sample, ids, proto):
    metadata = path if path.is_file() else path / "launcher_Data/il2cpp_data/Metadata/global-metadata.dat"
    data = metadata.read_bytes()
    check(digest(data), sample["metadata_sha256"], "client metadata SHA-256")
    if sample["metadata_evidence"] == "fingerprint_only":
        return
    check(sample["metadata_evidence"], "registry_and_req_rsp_tags", "metadata evidence level")
    observed, classes = extract_catalog(data)
    check(observed.pop("InValid"), -1, "retail invalid sentinel")
    check(observed, ids, "retail message registry")
    native = {name: {field.replace("_", "").lower(): tag for field, tag in fields.items()}
              for name, fields in classes.items()}
    fields = {message.name: {field.name.replace("_", "").lower(): field.number for field in message.field}
              for message in proto.message_type if message.name in ids and message.name.endswith(("Req", "Rsp"))}
    check(native, fields, "retail request/response field names and tags")


def check_lolo(path, profile, ids):
    source = profile["lolo"]
    with (path / source["registry"]).open(encoding="utf-8-sig", newline="") as reader:
        rows = list(csv.reader(reader))
    observed = {name: int(number) for name, number in rows}
    check(len(observed), len(rows), "Lolo registry unique names")
    check(observed, ids, "Lolo message registry")
    text = (path / source["mp_room_enum"]).read_text(encoding="utf-8")
    pairs = re.findall(r"^\s*MPPlayRoomEventType_(\w+)\s+MPPlayRoomEventType\s*=\s*(\d+)\s*$", text, re.M)
    check({name: int(number) for name, number in pairs}, profile["mp_room_event_enum"], "Lolo room event enum")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", nargs=2, action="append", default=[], metavar=("CHANNEL", "INSTALL_OR_METADATA"))
    parser.add_argument("--build-report", type=Path, help="UTF-8 JSON from inspect_client_build.py")
    parser.add_argument("--lolo", type=Path, help="Lolo source tree")
    parser.add_argument("--export-registry", type=Path, help="write canonical UTF-8 CSV without header, LF")
    args = parser.parse_args()
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    ids = server_ids(ROOT / profile["registry"]["source"])
    proto, file_hash = load_descriptor(ROOT / profile["protobuf"]["generated_source"])
    check(file_hash, profile["protobuf"]["descriptor_sha256"], "native FileDescriptorProto")
    schema = (ROOT / profile["protobuf"]["source"]).read_text(encoding="utf-8")
    check(digest(schema.encode()), profile["protobuf"]["schema_sha256_lf"], "native .proto")
    check_server(profile, ids, proto)
    print(f"OF_SF: {len(ids)} native messages, descriptor and selected routes OK")
    if args.build_report:
        report = json.loads(args.build_report.read_bytes())
        check(report["schema"], 1, "build report schema")
        for name, sample in profile["client_builds"].items():
            check(build_sample(report["clients"][name]), sample["build"], f"{name} retail manifest")
        print(f"Retail manifests OK: {', '.join(profile['client_builds'])}")
    for name, path in args.client:
        sample = profile["client_builds"][name]
        check_client(Path(path), sample, ids, proto)
        print(f"{name}: {sample['metadata_evidence']} OK")
    if args.lolo:
        check_lolo(args.lolo, profile, ids)
        print("Lolo registry and multiplayer enum OK")
    if args.export_registry:
        args.export_registry.write_bytes(registry_bytes(ids))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError) as error:
        sys.exit(str(error))
