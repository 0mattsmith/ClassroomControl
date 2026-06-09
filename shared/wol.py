"""Wake-on-LAN — send a magic packet to power on a remote machine.

Works without any client process running on the target: the magic
packet is interpreted by the target's network interface card directly,
which signals the motherboard to power on the system (assuming WoL is
enabled in BIOS and the OS has left the NIC powered).

Usage:

    from shared.wol import send_magic_packet
    send_magic_packet("AA:BB:CC:DD:EE:FF")
    send_magic_packet("AA-BB-CC-DD-EE-FF", broadcast="192.168.1.255")
"""

from __future__ import annotations

import binascii
import socket


def normalize_mac(mac: str) -> str:
    """Strip ``:``, ``-``, ``.`` and whitespace from a MAC string and
    return a 12-char uppercase hex string. Raises ``ValueError`` on
    anything else."""
    cleaned = "".join(c for c in (mac or "") if c.isalnum())
    if len(cleaned) != 12:
        raise ValueError(
            f"MAC address must be 12 hex chars (got {len(cleaned)!r}): {mac!r}"
        )
    # Will raise binascii.Error if non-hex
    binascii.unhexlify(cleaned)
    return cleaned.upper()


def build_magic_packet(mac: str) -> bytes:
    """Build the 102-byte magic packet for ``mac``."""
    mac_hex = normalize_mac(mac)
    mac_bytes = binascii.unhexlify(mac_hex)
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "255.255.255.255",
    port: int = 9,
) -> bool:
    """Send a Wake-on-LAN magic packet to ``mac``.

    ``broadcast`` defaults to the global LAN broadcast address. For a
    specific subnet pass the subnet's broadcast (e.g. ``192.168.1.255``).
    ``port`` is conventionally 7 (echo) or 9 (discard); the NIC ignores
    the port — it just inspects payload — but some routers filter on it.

    Returns True on success, False on socket failure.
    """
    try:
        packet = build_magic_packet(mac)
    except (ValueError, binascii.Error):
        return False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (broadcast, port))
            return True
        finally:
            sock.close()
    except OSError:
        return False
