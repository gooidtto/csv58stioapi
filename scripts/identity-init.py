#!/usr/bin/env python3
"""Initialize node identity exactly once on the mounted persistent volume.

The identity set is immutable after first successful initialization. Missing
identity files after initialization are treated as corruption and fail closed;
this prevents accidental node identity rotation on later restarts/redeploys.
"""
import json
import os
import secrets
import subprocess
from pathlib import Path

D = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", os.environ.get("DATA_DIR", "/data")))
D.mkdir(parents=True, exist_ok=True)

UUID_FILE = D / "uuid.txt"
PRIV_FILE = D / "reality_private_key.txt"
PUB_FILE = D / "reality_public_key.txt"
TOKEN_FILE = D / "subscription_token.txt"
IDS_FILE = D / "reality_short_ids.json"
MARKER = D / ".node-identity-initialized"

IDENTITY_FILES = (UUID_FILE, PRIV_FILE, PUB_FILE, TOKEN_FILE, IDS_FILE)


def atomic_write(path: Path, value: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(value.strip() + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def read_nonempty(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text().strip()


def identity_complete() -> bool:
    if not all(read_nonempty(p) for p in IDENTITY_FILES):
        return False
    try:
        ids = json.loads(read_nonempty(IDS_FILE))
    except Exception:
        return False
    return isinstance(ids, list) and len(ids) >= 3 and all(2 <= len(str(x)) <= 32 for x in ids)


def generate_identity() -> None:
    atomic_write(UUID_FILE, subprocess.check_output(["xray", "uuid"], text=True))

    raw = subprocess.check_output(["xray", "x25519"], text=True, stderr=subprocess.STDOUT)
    private = public = ""
    for line in raw.splitlines():
        if line.startswith("PrivateKey: "):
            private = line.split(": ", 1)[1].strip()
        elif line.startswith("Password: "):
            public = line.split(": ", 1)[1].strip()
    if not private or not public:
        raise RuntimeError("failed to generate REALITY key pair")
    atomic_write(PRIV_FILE, private)
    atomic_write(PUB_FILE, public)

    atomic_write(TOKEN_FILE, secrets.token_urlsafe(32))
    atomic_write(IDS_FILE, json.dumps([secrets.token_hex(6) for _ in range(3)], indent=2))


def main() -> None:
    marked = MARKER.is_file()
    complete = identity_complete()

    if marked:
        if not complete:
            raise SystemExit("FATAL: node identity was previously initialized but is incomplete; refusing to rotate identity")
        print(f"PERSISTENT_VOLUME={D}")
        print("NODE_IDENTITY=REUSED")
        return

    if complete:
        MARKER.write_text("identity initialized\n")
        os.chmod(MARKER, 0o600)
        print(f"PERSISTENT_VOLUME={D}")
        print("NODE_IDENTITY=REUSED_INITIALIZED")
        return

    if any(p.exists() for p in IDENTITY_FILES):
        raise SystemExit("FATAL: partial node identity found on an uninitialized volume; refusing to mix or replace identity")

    generate_identity()
    if not identity_complete():
        raise SystemExit("FATAL: node identity initialization did not produce a complete identity set")
    MARKER.write_text("identity initialized\n")
    os.chmod(MARKER, 0o600)
    print(f"PERSISTENT_VOLUME={D}")
    print("NODE_IDENTITY=INITIALIZED")


if __name__ == "__main__":
    main()
