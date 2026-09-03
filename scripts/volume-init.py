#!/usr/bin/env python3
"""Initialize a Railway Persistent Volume for stable node identity.

The script only bootstraps /data when the mounted volume is empty. It never
copies runtime endpoints or generated runtime artifacts from the image. Node
identity files are generated once and then preserved by the volume.
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path

D = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", os.environ.get("DATA_DIR", "/data")))
D.mkdir(parents=True, exist_ok=True)

UUID_FILE = D / "uuid.txt"
PRIV_FILE = D / "reality_private_key.txt"
PUB_FILE = D / "reality_public_key.txt"
TOKEN_FILE = D / "subscription_token.txt"
IDS_FILE = D / "reality_short_ids.json"
MARKER = D / ".node-identity-initialized"


def write_secret(path: Path, value: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(value.strip() + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def ensure_uuid() -> None:
    if UUID_FILE.is_file() and UUID_FILE.read_text().strip():
        return
    out = subprocess.check_output(["xray", "uuid"], text=True).strip()
    if not out:
        raise RuntimeError("xray uuid returned empty value")
    write_secret(UUID_FILE, out)


def ensure_reality_keys() -> None:
    if PRIV_FILE.is_file() and PUB_FILE.is_file() and PRIV_FILE.read_text().strip() and PUB_FILE.read_text().strip():
        return
    raw = subprocess.check_output(["xray", "x25519"], text=True, stderr=subprocess.STDOUT)
    private = ""
    public = ""
    for line in raw.splitlines():
        if line.startswith("PrivateKey: "):
            private = line.split(": ", 1)[1].strip()
        elif line.startswith("Password: "):
            public = line.split(": ", 1)[1].strip()
    if not private or not public:
        raise RuntimeError("xray x25519 did not return both REALITY keys")
    write_secret(PRIV_FILE, private)
    write_secret(PUB_FILE, public)


def ensure_token() -> None:
    if TOKEN_FILE.is_file() and TOKEN_FILE.read_text().strip():
        return
    write_secret(TOKEN_FILE, secrets.token_urlsafe(32))


def ensure_short_ids() -> None:
    import json
    if IDS_FILE.is_file():
        try:
            ids = json.loads(IDS_FILE.read_text())
        except Exception:
            ids = []
        if isinstance(ids, list):
            ids = [str(x) for x in ids]
            ids = [x for x in ids if len(x) >= 2 and len(x) <= 32]
            if len(ids) >= 3:
                return
    write_secret(IDS_FILE, json.dumps([secrets.token_hex(6) for _ in range(3)], indent=2))


if __name__ == "__main__":
    ensure_uuid()
    ensure_reality_keys()
    ensure_token()
    ensure_short_ids()
    MARKER.write_text("identity initialized\n")
    os.chmod(MARKER, 0o600)
    print(f"PERSISTENT_VOLUME={D}")
    print("NODE_IDENTITY=READY")
