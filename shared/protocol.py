"""
Wire protocol for ClassControl (Veyon-style classroom management).

Frame format (all integers big-endian):
    [4 bytes header_len][header_json][4 bytes payload_len][payload_bytes]

header_json is a UTF-8 JSON object with at minimum {"op": "<opcode>"}.
payload_bytes is opaque binary (image data, file chunks, etc.).

Transport: TCP with TLS. After TLS handshake the client (server-side listener)
issues a 32-byte random challenge; the connecting peer must respond with
HMAC-SHA256(shared_key, challenge). After auth, peers exchange OP frames.

All opcodes are defined in the Op class below. Both master->client and
client->master may use the same frame format; client replies typically echo
the opcode with a "status" field set to "ok" or "error".
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import ssl
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 11400          # mirrors Veyon's default service port
PROTOCOL_VERSION = 1
MAX_HEADER_BYTES = 64 * 1024            # 64 KB JSON header cap
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024    # 64 MB payload cap (file transfer)
CHALLENGE_BYTES = 32


class Op:
    """Opcodes exchanged between master and client."""

    # Connection / housekeeping
    HELLO = "hello"                 # first message after auth, includes peer info
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    ACK = "ack"

    # Screen monitoring & remote control
    GET_SCREENSHOT = "get_screenshot"
    SCREENSHOT = "screenshot"
    START_STREAM = "start_stream"
    STOP_STREAM = "stop_stream"
    STREAM_FRAME = "stream_frame"
    INPUT_EVENT = "input_event"     # remote-control mouse/keyboard

    # Demo mode (teacher screen broadcast)
    DEMO_START = "demo_start"
    DEMO_FRAME = "demo_frame"
    DEMO_STOP = "demo_stop"

    # Lock / message / notification
    LOCK = "lock"
    UNLOCK = "unlock"
    MESSAGE = "message"

    # File transfer
    FILE_PUSH = "file_push"         # master -> client (single file or chunk)
    FILE_PULL_REQUEST = "file_pull_request"   # master asks for a file
    FILE_PULL_RESPONSE = "file_pull_response"
    FILE_LIST = "file_list"
    FILE_LIST_RESULT = "file_list_result"

    # Application / URL launch
    LAUNCH = "launch"               # open app, file or URL

    # Blocking / lockdown
    SET_BLOCKED_APPS = "set_blocked_apps"
    SET_BLOCKED_URLS = "set_blocked_urls"
    INTERNET_LOCKDOWN = "internet_lockdown"
    INTERNET_RELEASE = "internet_release"
    # Running-app inspection / remote kill
    GET_RUNNING_APPS = "get_running_apps"
    RUNNING_APPS = "running_apps"
    KILL_APP = "kill_app"

    # Power management
    POWER = "power"                 # action: shutdown|restart|sleep|logout|wake

    # Audio
    AUDIO = "audio"                 # action: mute|unmute|volume; value: 0-100

    # Info / inventory
    INFO = "info"
    INFO_RESULT = "info_result"


# ---------------------------------------------------------------------------
# Framing primitives
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """A decoded protocol frame."""

    header: dict
    payload: bytes = b""

    @property
    def op(self) -> str:
        return self.header.get("op", "")


async def read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``reader`` or raise ``ConnectionError``."""
    data = await reader.readexactly(n)
    return data


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    """Read one Frame from the stream."""
    raw = await read_exact(reader, 4)
    header_len = int.from_bytes(raw, "big")
    if header_len == 0 or header_len > MAX_HEADER_BYTES:
        raise ConnectionError(f"invalid header length: {header_len}")
    header_bytes = await read_exact(reader, header_len)
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise ConnectionError(f"bad header JSON: {exc}") from exc
    raw = await read_exact(reader, 4)
    payload_len = int.from_bytes(raw, "big")
    if payload_len > MAX_PAYLOAD_BYTES:
        raise ConnectionError(f"payload too large: {payload_len}")
    payload = await read_exact(reader, payload_len) if payload_len else b""
    return Frame(header=header, payload=payload)


async def write_frame(
    writer: asyncio.StreamWriter,
    op: str,
    data: dict | None = None,
    payload: bytes = b"",
) -> None:
    """Serialize and write a single frame."""
    header = {"op": op, "v": PROTOCOL_VERSION}
    if data:
        header.update(data)
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    writer.write(len(header_bytes).to_bytes(4, "big"))
    writer.write(header_bytes)
    writer.write(len(payload).to_bytes(4, "big"))
    if payload:
        writer.write(payload)
    await writer.drain()


# ---------------------------------------------------------------------------
# Authentication (HMAC challenge/response)
# ---------------------------------------------------------------------------


def load_or_create_key(path: str) -> bytes:
    """Load a hex-encoded 32-byte shared key, generating one if absent."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return bytes.fromhex(fh.read().strip())
    key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key.hex())
    try:
        os.chmod(path, 0o600)
    except Exception:  # pragma: no cover - non-fatal on some FS
        pass
    return key


def hmac_response(key: bytes, challenge: bytes) -> bytes:
    return hmac.new(key, challenge, hashlib.sha256).digest()


def key_fingerprint(key: bytes) -> str:
    """Short visible fingerprint of the shared key.

    Returns the first 12 hex characters of ``sha256(key)``. Logged at
    startup on both master and client so an operator can verify they're
    using the same key without exposing the key itself.
    """
    return hashlib.sha256(key).hexdigest()[:12]


async def server_authenticate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    key: bytes,
) -> bool:
    """Called by the client daemon (acting as server) to verify the master."""
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    await write_frame(writer, "auth_challenge", {"challenge": challenge.hex()})
    frame = await read_frame(reader)
    if frame.op != "auth_response":
        return False
    expected = hmac_response(key, challenge)
    given = bytes.fromhex(frame.header.get("response", ""))
    ok = hmac.compare_digest(expected, given)
    await write_frame(writer, "auth_result", {"ok": ok})
    return ok


async def client_authenticate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    key: bytes,
) -> bool:
    """Called by the master to authenticate to a client daemon."""
    frame = await read_frame(reader)
    if frame.op != "auth_challenge":
        return False
    challenge = bytes.fromhex(frame.header["challenge"])
    response = hmac_response(key, challenge)
    await write_frame(writer, "auth_response", {"response": response.hex()})
    result = await read_frame(reader)
    return bool(result.header.get("ok"))


# ---------------------------------------------------------------------------
# TLS context helpers
# ---------------------------------------------------------------------------


def make_server_ssl(cert_path: str, key_path: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    # Self-signed; client-side trust is established via shared-key auth.
    ctx.check_hostname = False
    return ctx


def make_client_ssl() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # TOFU model; HMAC auth provides identity
    return ctx


def ensure_self_signed(cert_path: str, key_path: str, cn: str = "classcontrol") -> None:
    """Generate a self-signed cert + key if they do not already exist."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    os.makedirs(os.path.dirname(cert_path) or ".", exist_ok=True)
    # Lazy import so we don't require cryptography for clients that already have certs.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime as _dt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ClassControl"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_dt.datetime.utcnow() - _dt.timedelta(days=1))
        .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as fh:
        fh.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Helper: connection wrapper for the master side
# ---------------------------------------------------------------------------


class PeerConnection:
    """Convenience wrapper around an authenticated peer connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()

    async def send(self, op: str, data: dict | None = None, payload: bytes = b"") -> None:
        async with self._lock:
            await write_frame(self.writer, op, data, payload)

    async def recv(self) -> Frame:
        return await read_frame(self.reader)

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


__all__ = [
    "DEFAULT_PORT",
    "PROTOCOL_VERSION",
    "Op",
    "Frame",
    "read_frame",
    "write_frame",
    "load_or_create_key",
    "server_authenticate",
    "client_authenticate",
    "make_server_ssl",
    "make_client_ssl",
    "ensure_self_signed",
    "PeerConnection",
]
