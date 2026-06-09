"""
Connection manager that maintains an authenticated TLS link to every
computer in the roster. Runs on a dedicated asyncio thread; UI code
talks to it via thread-safe helpers (``submit`` and ``broadcast``).

The Qt UI subscribes to ``ConnectionHub.signals`` to receive:

* connectionStateChanged(computer_id, state, info)
* frameReceived(computer_id, jpeg_bytes)        # thumbnails + remote control
* messageFromClient(computer_id, op, header)    # generic frame router
* fileFromClient(computer_id, name, bytes)
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from shared import config, protocol
from shared.protocol import (
    Op, PeerConnection, client_authenticate, make_client_ssl,
    load_or_create_key, read_frame, write_frame,
)
from master.roster import Computer
from master import activity_log


class HubSignals(QObject):
    connectionStateChanged = pyqtSignal(str, str, dict)
    frameReceived = pyqtSignal(str, bytes)
    messageFromClient = pyqtSignal(str, str, dict)
    # cid, filename, raw bytes, response header (so the master can read
    # extras like is_folder / file_count without depending on signal order)
    fileFromClient = pyqtSignal(str, str, bytes, dict)
    activity = pyqtSignal(str)


@dataclass
class PeerState:
    computer: Computer
    conn: Optional[PeerConnection] = None
    state: str = "disconnected"   # disconnected | connecting | connected | error
    info: dict | None = None
    reader_task: Optional[asyncio.Task] = None
    reconnect_task: Optional[asyncio.Task] = None
    last_error: str = ""


class ConnectionHub:
    def __init__(self):
        self.signals = HubSignals()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(
            target=self._run_loop, name="classcontrol-master-asyncio", daemon=True,
        )
        self._ready = threading.Event()
        self._peers: dict[str, PeerState] = {}
        self._peers_lock = threading.Lock()
        self._key = load_or_create_key(str(config.key_path("master")))
        self._ssl = make_client_ssl()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def shutdown(self) -> None:
        if not self.loop:
            return
        for cid in list(self._peers.keys()):
            self.disconnect(cid)
        self.loop.call_soon_threadsafe(self.loop.stop)

    # ------------------------------------------------------------------
    # Submit work from the GUI thread onto the asyncio loop
    # ------------------------------------------------------------------

    def submit(self, coro: Awaitable) -> Future:
        if not self.loop:
            raise RuntimeError("hub not started")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ------------------------------------------------------------------
    # Peer management
    # ------------------------------------------------------------------

    def add_computer(self, computer: Computer, auto_connect: bool = True) -> None:
        with self._peers_lock:
            self._peers[computer.id] = PeerState(computer=computer)
        if auto_connect:
            self.connect(computer.id)

    def remove_computer(self, computer_id: str) -> None:
        self.disconnect(computer_id)
        with self._peers_lock:
            self._peers.pop(computer_id, None)

    def computer_ids(self) -> list[str]:
        with self._peers_lock:
            return list(self._peers.keys())

    def get_state(self, computer_id: str) -> Optional[PeerState]:
        with self._peers_lock:
            return self._peers.get(computer_id)

    def connect(self, computer_id: str) -> None:
        self.submit(self._connect(computer_id))

    def disconnect(self, computer_id: str) -> None:
        self.submit(self._disconnect(computer_id))

    async def _connect(self, computer_id: str) -> None:
        peer = self._peers.get(computer_id)
        if not peer or peer.state == "connected":
            return
        peer.state = "connecting"
        self.signals.connectionStateChanged.emit(computer_id, peer.state, {})
        try:
            reader, writer = await asyncio.open_connection(
                host=peer.computer.host, port=peer.computer.port, ssl=self._ssl,
            )
            ok = await client_authenticate(reader, writer, self._key)
            if not ok:
                raise ConnectionError("authentication failed (wrong shared key?)")
            peer.conn = PeerConnection(reader, writer)
            peer.state = "connected"
            peer.last_error = ""
            # First frame from client is HELLO with info
            frame = await peer.conn.recv()
            if frame.op == Op.HELLO:
                peer.info = frame.header.get("info", {})
            self.signals.connectionStateChanged.emit(
                computer_id, peer.state, peer.info or {}
            )
            peer.reader_task = asyncio.create_task(self._reader_loop(computer_id))
        except Exception as exc:
            peer.state = "error"
            peer.last_error = str(exc)
            self.signals.connectionStateChanged.emit(
                computer_id, peer.state, {"error": str(exc)}
            )

    async def _disconnect(self, computer_id: str) -> None:
        peer = self._peers.get(computer_id)
        if not peer:
            return
        if peer.reader_task and not peer.reader_task.done():
            peer.reader_task.cancel()
        if peer.conn:
            await peer.conn.close()
        peer.conn = None
        peer.state = "disconnected"
        self.signals.connectionStateChanged.emit(computer_id, peer.state, {})

    async def _reader_loop(self, computer_id: str) -> None:
        peer = self._peers.get(computer_id)
        if not peer or not peer.conn:
            return
        try:
            while True:
                frame = await peer.conn.recv()
                if frame.op in (Op.STREAM_FRAME, Op.SCREENSHOT):
                    self.signals.frameReceived.emit(computer_id, frame.payload)
                elif frame.op == Op.FILE_PULL_RESPONSE and frame.header.get("ok"):
                    self.signals.fileFromClient.emit(
                        computer_id,
                        frame.header.get("name", "file.bin"),
                        frame.payload,
                        frame.header,
                    )
                # Always re-emit so dialogs awaiting specific replies can pick them up
                self.signals.messageFromClient.emit(
                    computer_id, frame.op, frame.header
                )
        except (asyncio.IncompleteReadError, ConnectionError, ConnectionResetError) as exc:
            peer.state = "disconnected"
            peer.last_error = str(exc)
            self.signals.connectionStateChanged.emit(
                computer_id, peer.state, {"error": str(exc)}
            )

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def send(self, computer_id: str, op: str, data: dict | None = None,
             payload: bytes = b"") -> None:
        self.submit(self._send(computer_id, op, data, payload))

    async def _send(self, computer_id: str, op: str, data, payload):
        peer = self._peers.get(computer_id)
        if not peer or not peer.conn or peer.state != "connected":
            return
        try:
            await peer.conn.send(op, data, payload)
        except Exception as exc:
            peer.state = "error"
            peer.last_error = str(exc)
            self.signals.connectionStateChanged.emit(
                computer_id, peer.state, {"error": str(exc)}
            )

    def broadcast(self, op: str, data: dict | None = None,
                  payload: bytes = b"") -> None:
        with self._peers_lock:
            ids = list(self._peers.keys())
        for cid in ids:
            self.send(cid, op, data, payload)
        activity_log.log(f"broadcast:{op}", target="all", detail=data or {})
        self.signals.activity.emit(f"broadcast {op} ({len(ids)} targets)")

    def send_logged(self, computer_id: str, op: str, data: dict | None = None,
                    payload: bytes = b"") -> None:
        self.send(computer_id, op, data, payload)
        peer = self._peers.get(computer_id)
        target = peer.computer.name if peer else computer_id
        activity_log.log(op, target=target, detail=data or {})
        self.signals.activity.emit(f"{op} -> {target}")
