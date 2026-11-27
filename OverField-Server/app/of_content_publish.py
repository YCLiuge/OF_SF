"""Local operator commands for the optional OF-SF extension catalog."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from of_contracts import CONTENT_FILE, MAX_BYTES, encode_catalog


def run(operation, source, state):
    with source.open("rb") as stream:
        encoded = encode_catalog(stream.read(MAX_BYTES + 1))
    result = {"items": len(json.loads(encoded)["items"]),
              "content_manifest": hashlib.sha256(encoded).hexdigest()}
    if operation == "validate":
        return result
    if operation != "publish":
        raise ValueError("Unknown catalog operation")
    state.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with NamedTemporaryFile(dir=state, prefix=".of-content-", delete=False) as output:
            staged = Path(output.name)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged, state / CONTENT_FILE)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return result
