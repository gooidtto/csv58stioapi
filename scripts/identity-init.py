#!/usr/bin/env python3
"""Initialize node identity exactly once on the mounted persistent volume.

Identity is a persistent, immutable deployment primitive. A brand-new empty
volume may be initialized once. Once the marker exists, the complete identity
must remain present and valid; otherwise startup fails closed and no identity
is regenerated.
"""
import hashlib
import json
import os
import re
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
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
REALITY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
SHORT_ID_RE = re.compile(r"^[0-9a-fA-F]{2,32}$")


def persistent_mount_present(path: Path) -> bool:
    """Require the identity directory to be an actual mount point.

    A Railway Volume must be mounted here before identity is ever initialized.
    Checking /proc/self/mountinfo avoids relying on the optional `mountpoint`
    utility, which is not installed in the Alpine runtime image.
    """
    try:
        target = str(path.resolve())
        if target == "/":
            return False
        if not os.path.ismount(target):
            return False
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as fh:
            for line in fh:
                fields = line.split()
                if len(fields) >= 6 and fields[4] == target:
                    return True
    except (OSError, ValueError):
        return False
    return False


def require_persistent_mount() -> None:
    if not persistent_mount_present(D):
        raise SystemExit(
            f"FATAL: {D} is not a mounted persistent volume; "
            "refusing to initialize or run node identity"
        )


def atomic_write(path: Path, value: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(value.strip() + "\n")
        os.chmod(tmp, 0o600)
        with tmp.open("r+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def read_nonempty(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def identity_complete() -> bool:
    uuid = read_nonempty(UUID_FILE)
    private = read_nonempty(PRIV_FILE)
    public = read_nonempty(PUB_FILE)
    token = read_nonempty(TOKEN_FILE)
    ids_raw = read_nonempty(IDS_FILE)

    if not UUID_RE.fullmatch(uuid):
        return False
    if not REALITY_KEY_RE.fullmatch(private):
        return False
    if not REALITY_KEY_RE.fullmatch(public):
        return False
    if not token or len(token) < 20 or len(token) > 256:
        return False
    try:
        ids = json.loads(ids_raw)
    except Exception:
        return False
    if not isinstance(ids, list) or len(ids) != 3:
        return False
    return all(isinstance(x, str) and SHORT_ID_RE.fullmatch(x) for x in ids)


def identity_fingerprint() -> str:
    """Return a non-secret stable fingerprint for deployment log verification."""
    uuid = read_nonempty(UUID_FILE)
    public = read_nonempty(PUB_FILE)
    digest = hashlib.sha256(f"{uuid}\n{public}".encode()).hexdigest()
    return digest[:16]


def emit_identity_status(state: str) -> None:
    print(f"PERSISTENT_VOLUME={D}")
    print("PERSISTENT_VOLUME_MOUNT=PASS")
    print(f"NODE_IDENTITY={state}")
    print(f"NODE_IDENTITY_FINGERPRINT={identity_fingerprint()}")


def generate_identity() -> None:
    uuid = subprocess.check_output(["xray", "uuid"], text=True).strip()
    if not UUID_RE.fullmatch(uuid):
        raise RuntimeError("xray generated an invalid UUID")

    raw = subprocess.check_output(["xray", "x25519"], text=True, stderr=subprocess.STDOUT)
    private = public = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("PrivateKey:"):
            private = line.split(":", 1)[1].strip()
        elif line.startswith("Password (PublicKey):"):
            public = line.split(":", 1)[1].strip()
        elif line.startswith("Password:"):
            public = line.split(":", 1)[1].strip()
        elif line.startswith("PublicKey:"):
            public = line.split(":", 1)[1].strip()
    if not REALITY_KEY_RE.fullmatch(private) or not REALITY_KEY_RE.fullmatch(public):
        raise RuntimeError("xray generated an invalid REALITY key pair")

    token = secrets.token_urlsafe(32)
    ids = [secrets.token_hex(6) for _ in range(3)]

    # Every individual file is atomically replaced and fsynced. The marker is
    # written last, so an interrupted first initialization can never be mistaken
    # for a successfully initialized identity. Any incomplete set is fail-closed.
    atomic_write(UUID_FILE, uuid)
    atomic_write(PRIV_FILE, private)
    atomic_write(PUB_FILE, public)
    atomic_write(TOKEN_FILE, token)
    atomic_write(IDS_FILE, json.dumps(ids, separators=(",", ":")))


def write_marker() -> None:
    atomic_write(MARKER, "identity initialized")


def main() -> None:
    require_persistent_mount()

    marked = MARKER.is_file()
    complete = identity_complete()

    if marked:
        if not complete:
            raise SystemExit(
                "FATAL: node identity was previously initialized but is missing or invalid; "
                "refusing to rotate identity"
            )
        emit_identity_status("REUSED")
        return

    if complete:
        write_marker()
        emit_identity_status("REUSED_INITIALIZED")
        return

    if any(p.exists() for p in IDENTITY_FILES):
        raise SystemExit(
            "FATAL: partial or invalid node identity found on an uninitialized volume; "
            "refusing to mix, repair, or replace identity"
        )

    generate_identity()
    if not identity_complete():
        raise SystemExit("FATAL: node identity initialization did not produce a complete identity set")
    write_marker()
    emit_identity_status("INITIALIZED")


if __name__ == "__main__":
    main()
