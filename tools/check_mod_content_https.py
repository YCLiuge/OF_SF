"""Run the real Mod transport against an isolated OF_SF HTTPS catalog.

Use --server for an unpacked OF_SF distribution and --probe for the built
overfiled-mod tools/OfContractsVerification/Verify.dll. Requires dotnet and OpenSSL.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def verify(server, probe, example):
    sys.path[:0] = [str(server / name) for name in ("app", "app/upstream", "tests", "vendor/common",
                    "vendor/win64" if os.name == "nt" else "vendor/linux-x86_64")]
    from local_identity import LocalIdentity
    import of_contracts
    from test_native_player_gateway import certificates, gateway
    from test_private_profiles import serve

    source = json.loads(of_contracts.encode_catalog(example.read_bytes()))
    expected = [{"Id": item["id"], "Title": item["title"], "Text": item.get("text"),
                 "Entry": item.get("entry")} for item in source["items"]]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        certs = certificates(root)
        if certs is None:
            raise RuntimeError("OpenSSL required for HTTPS integration")
        catalog = root / of_contracts.CONTENT_FILE
        staging = catalog.with_suffix(".new")

        def operator(operation, source, succeeds=True):
            result = subprocess.run(
                [sys.executable, "-B", str(server / "of.py"), "--state", str(root),
                 "content", operation, str(source)], capture_output=True, timeout=15)
            if not succeeds:
                assert result.returncode != 0, "Invalid catalog was accepted"
                return
            if result.returncode:
                raise AssertionError(result.stderr.decode("utf-8", "replace"))
            return json.loads(result.stdout)

        with serve(LocalIdentity(root / "identity.sqlite3")) as backend:
            with gateway(backend, 1, certs, [], root) as tls_port:
                command = ["dotnet", str(probe), "--probe", f"https://localhost:{tls_port}"]
                def read(should_succeed=True, trusted=True):
                    args = command + (["--ca", str(certs / "ca.pem")] if trusted else [])
                    process = subprocess.run(args, cwd=root, capture_output=True, timeout=25)
                    if should_succeed:
                        if process.returncode:
                            raise AssertionError(process.stderr.decode("utf-8", "replace"))
                        return json.loads(process.stdout.decode("utf-8-sig"))
                    assert process.returncode != 0, "Mod accepted an unavailable or untrusted realm"

                assert read() == []
                read(False, trusted=False)
                checked = operator("validate", example)
                assert not catalog.exists()
                assert operator("publish", example) == checked
                assert checked["content_manifest"] == hashlib.sha256(catalog.read_bytes()).hexdigest()
                assert read() == expected
                staging.write_bytes(b"{}")
                operator("publish", staging, succeeds=False)
                assert read() == expected
                staging.replace(catalog)  # Exercise recovery from direct operator edits, too.
                read(False)
                operator("publish", example)
                assert read() == expected
                catalog.unlink()
                assert read() == []
    print(f"PASS Mod HTTPS probe: empty defaults, private test CA, {len(expected)} entries "
          "with exact titles/text/action keys, invalid catalog, recovery and removal.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for parameter in ("server", "probe", "catalog"):
        parser.add_argument("--" + parameter, type=Path, required=True)
    args = parser.parse_args()
    verify(args.server.resolve(), args.probe.resolve(), args.catalog.resolve())
