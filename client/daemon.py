"""
ClassControl client daemon.

Runs on the student machine. Accepts a single authenticated TLS
connection from the teacher's master at a time and serves the full
command set (screen capture, input injection, demo overlay, lock,
messaging, file transfer, blocking, internet lockdown, power, etc).

Implementation notes
--------------------
* The asyncio server lives on a worker thread; the Qt event loop owns
  the main thread because the overlays must be created from the GUI
  thread on macOS.
* Communication from the asyncio side to the Qt side goes through the
  ``OverlayController`` QObject's signals, which Qt automatically marshals
  with ``Qt::QueuedConnection`` across threads.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

# Allow `python -m client.daemon` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared import config, logging_setup, protocol
from shared.protocol import (
    Op, read_frame, write_frame, server_authenticate, make_server_ssl,
    ensure_self_signed, load_or_create_key, key_fingerprint,
)
from client import overlay
from client import platform as p   # picks macos or windows backends
# Backwards-compatible aliases so the dispatch code reads naturally.
mac_screen = p.screen
mac_input = p.input_inject
mac_internet = p.internet
mac_block = p.blocking
mac_power = p.power
mac_audio = p.audio
mac_launcher = p.launcher
mac_info = p.info


LOG = logging_setup.configure(
    "classcontrol.client",
    config.user_config_dir("client") / "client.log",
)


# ---------------------------------------------------------------------------
# Connection handler
# ---------------------------------------------------------------------------


class ClientSession:
    """One authenticated connection from a master."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        overlay_ctrl: overlay.OverlayController,
    ):
        self.reader = reader
        self.writer = writer
        self.overlay = overlay_ctrl
        self.peer_ip = writer.get_extra_info("peername")[0] if writer.get_extra_info("peername") else "?"
        self.stream_task: Optional[asyncio.Task] = None
        self.stream_fps: float = 4.0
        self.stream_max_w: int = 0          # 0 = native resolution
        self.stream_quality: int = 92
        # JPEG default — works with every Pillow build and every Qt build.
        # WebP is opt-in (Pillow-without-webp or Qt-without-webp-plugin
        # would silently break the stream).
        self.stream_format: str = "JPEG"

    async def run(self) -> None:
        LOG.info("session start from %s", self.peer_ip)
        try:
            await write_frame(
                self.writer, Op.HELLO,
                {"info": mac_info.collect()},
            )
            while True:
                frame = await read_frame(self.reader)
                await self._dispatch(frame)
        except (asyncio.IncompleteReadError, ConnectionError, ConnectionResetError) as exc:
            LOG.info("session ended (%s): %s", self.peer_ip, exc)
        except Exception:  # pragma: no cover - defensive
            LOG.exception("unhandled error in session")
        finally:
            await self._stop_stream()
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, frame: protocol.Frame) -> None:
        op = frame.op
        data = frame.header
        payload = frame.payload

        handler = {
            Op.PING: self._h_ping,
            Op.GET_SCREENSHOT: self._h_screenshot,
            Op.START_STREAM: self._h_start_stream,
            Op.STOP_STREAM: self._h_stop_stream,
            Op.INPUT_EVENT: self._h_input,
            Op.LOCK: self._h_lock,
            Op.UNLOCK: self._h_unlock,
            Op.MESSAGE: self._h_message,
            Op.DEMO_START: self._h_demo_start,
            Op.DEMO_FRAME: self._h_demo_frame,
            Op.DEMO_STOP: self._h_demo_stop,
            Op.FILE_PUSH: self._h_file_push,
            Op.FILE_PULL_REQUEST: self._h_file_pull,
            Op.FILE_LIST: self._h_file_list,
            Op.LAUNCH: self._h_launch,
            Op.SET_BLOCKED_APPS: self._h_set_blocked_apps,
            Op.SET_BLOCKED_URLS: self._h_set_blocked_urls,
            Op.INTERNET_LOCKDOWN: self._h_internet_lockdown,
            Op.INTERNET_RELEASE: self._h_internet_release,
            Op.GET_RUNNING_APPS: self._h_get_running_apps,
            Op.KILL_APP: self._h_kill_app,
            Op.POWER: self._h_power,
            Op.AUDIO: self._h_audio,
            Op.INFO: self._h_info,
        }.get(op)

        if handler is None:
            await write_frame(self.writer, Op.ERROR, {"reason": f"unknown op: {op}"})
            return
        try:
            await handler(data, payload)
        except Exception as exc:
            LOG.exception("handler %s failed", op)
            await write_frame(self.writer, Op.ERROR, {"reason": str(exc), "for": op})

    # ------------------------------------------------------------------
    # Individual handlers
    # ------------------------------------------------------------------

    async def _h_ping(self, data, payload):
        await write_frame(self.writer, Op.PONG, {"t": time.time()})

    async def _h_info(self, data, payload):
        await write_frame(self.writer, Op.INFO_RESULT, {"info": mac_info.collect()})

    async def _h_screenshot(self, data, payload):
        mw = int(data.get("max_width", 0))
        q = int(data.get("quality", 90))
        fmt = str(data.get("format", "WEBP")).upper()
        blob = mac_screen.capture_screen_jpeg(
            max_width=mw, quality=q, fmt=fmt,
        ) or b""
        await write_frame(
            self.writer, Op.SCREENSHOT,
            {"size": len(blob), "format": fmt}, blob,
        )

    async def _h_start_stream(self, data, payload):
        self.stream_fps = float(data.get("fps", 4.0))
        self.stream_max_w = int(data.get("max_width", 0))
        self.stream_quality = int(data.get("quality", 90))
        self.stream_format = str(data.get("format", "WEBP")).upper()
        await self._stop_stream()
        self.stream_task = asyncio.create_task(self._streamer())
        await write_frame(self.writer, Op.ACK, {"for": Op.START_STREAM})

    async def _h_stop_stream(self, data, payload):
        await self._stop_stream()
        await write_frame(self.writer, Op.ACK, {"for": Op.STOP_STREAM})

    async def _stop_stream(self):
        if self.stream_task and not self.stream_task.done():
            self.stream_task.cancel()
            try:
                await self.stream_task
            except (asyncio.CancelledError, Exception):
                pass
        self.stream_task = None

    async def _streamer(self):
        interval = 1.0 / max(0.5, self.stream_fps)
        LOG.info(
            "stream started: fps=%s max_width=%s quality=%s format=%s",
            self.stream_fps, self.stream_max_w,
            self.stream_quality, self.stream_format,
        )
        consecutive_capture_fails = 0
        try:
            while True:
                blob = b""
                try:
                    blob = mac_screen.capture_screen_jpeg(
                        max_width=self.stream_max_w,
                        quality=self.stream_quality,
                        fmt=self.stream_format,
                    ) or b""
                except Exception:
                    # An exception here used to kill the streamer task
                    # silently — that's how the "waiting for frames"
                    # bug surfaced. Now we log and keep ticking.
                    LOG.exception("screen capture failed (will retry next tick)")
                if not blob:
                    consecutive_capture_fails += 1
                    if consecutive_capture_fails == 1 or consecutive_capture_fails % 30 == 0:
                        LOG.warning(
                            "no frame produced (consecutive_fails=%d). "
                            "Check Pillow/Quartz/mss availability and "
                            "Screen Recording permission.",
                            consecutive_capture_fails,
                        )
                else:
                    if consecutive_capture_fails:
                        LOG.info("frame production resumed after %d fail(s)",
                                 consecutive_capture_fails)
                    consecutive_capture_fails = 0
                    try:
                        await write_frame(
                            self.writer, Op.STREAM_FRAME,
                            {"size": len(blob), "t": time.time(),
                             "format": self.stream_format},
                            blob,
                        )
                    except (ConnectionError, ConnectionResetError):
                        LOG.info("streamer: peer disconnected, stopping")
                        return
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception:
            LOG.exception("streamer crashed unexpectedly")

    async def _h_input(self, data, payload):
        kind = data.get("kind")
        if kind == "mouse":
            mac_input.inject_mouse(
                data.get("event", "move"),
                float(data.get("x", 0.0)),
                float(data.get("y", 0.0)),
                data.get("button", "left"),
            )
        elif kind == "scroll":
            mac_input.inject_scroll(int(data.get("dy", 0)), int(data.get("dx", 0)))
        elif kind == "key":
            mac_input.inject_key(
                data.get("key", ""),
                bool(data.get("pressed", True)),
                data.get("text") or None,
                data.get("modifiers") or [],
            )

    async def _h_lock(self, data, payload):
        message = data.get("message", "Screen locked by teacher")
        # Strict = kiosk mode (hide dock + menu bar, block Cmd-Tab / Force-Quit /
        # session termination). Default to True so misuse fails closed —
        # the master explicitly sets False for the loopback case.
        strict = bool(data.get("strict", True))
        self.overlay.requestLock.emit(message, strict)
        await write_frame(self.writer, Op.ACK, {"for": Op.LOCK, "strict": strict})

    async def _h_unlock(self, data, payload):
        self.overlay.requestUnlock.emit()
        await write_frame(self.writer, Op.ACK, {"for": Op.UNLOCK})

    async def _h_message(self, data, payload):
        self.overlay.requestMessage.emit(
            data.get("title", "Message from teacher"),
            data.get("body", ""),
        )
        await write_frame(self.writer, Op.ACK, {"for": Op.MESSAGE})

    async def _h_demo_start(self, data, payload):
        windowed = bool(data.get("windowed", False))
        self.overlay.requestDemoStart.emit(windowed)
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.DEMO_START, "windowed": windowed},
        )

    async def _h_demo_frame(self, data, payload):
        if payload:
            self.overlay.requestDemoFrame.emit(payload)

    async def _h_demo_stop(self, data, payload):
        self.overlay.requestDemoStop.emit()
        await write_frame(self.writer, Op.ACK, {"for": Op.DEMO_STOP})

    # --------------------- File transfer ----------------------------

    async def _h_file_push(self, data, payload):
        dest_dir = config.shared_files_dir("client")
        name = os.path.basename(data.get("name", "file.bin"))
        path = dest_dir / name
        with open(path, "wb") as fh:
            fh.write(payload)

        # Honour the post-save action chosen by the teacher.
        action = (data.get("post_action") or "none").lower()
        try:
            if action == "open":
                mac_launcher.open_target(str(path))
            elif action == "reveal":
                mac_launcher.reveal_target(str(path))
            # "none" or anything unknown: just save, do nothing.
        except Exception:
            LOG.exception("post-save action %r failed", action)

        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.FILE_PUSH, "saved_to": str(path),
             "size": len(payload), "post_action": action},
        )

    async def _h_file_pull(self, data, payload):
        import io
        import zipfile

        rel = data.get("path", "")
        share = config.shared_files_dir("client")
        candidate = Path(rel) if os.path.isabs(rel) else share / rel
        try:
            candidate = candidate.resolve()
        except Exception:
            pass

        # --- Folder pull: zip recursively and return as one big payload ---
        if candidate.is_dir():
            buf = io.BytesIO()
            file_count = 0
            try:
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in candidate.rglob("*"):
                        if p.is_file():
                            arc = p.relative_to(candidate)
                            try:
                                zf.write(p, arcname=str(arc))
                                file_count += 1
                            except OSError:
                                continue
            except Exception as exc:
                await write_frame(
                    self.writer, Op.FILE_PULL_RESPONSE,
                    {"path": str(candidate), "ok": False,
                     "reason": f"zip failed: {exc}"},
                )
                return
            blob = buf.getvalue()
            await write_frame(
                self.writer, Op.FILE_PULL_RESPONSE,
                {"path": str(candidate), "ok": True,
                 "size": len(blob),
                 "name": candidate.name + ".zip",
                 "is_folder": True,
                 "file_count": file_count},
                blob,
            )
            return

        # --- Single-file pull (original behaviour) ---
        if not candidate.is_file():
            await write_frame(
                self.writer, Op.FILE_PULL_RESPONSE,
                {"path": str(candidate), "ok": False, "reason": "not found"},
            )
            return

        data_bytes = candidate.read_bytes()
        await write_frame(
            self.writer, Op.FILE_PULL_RESPONSE,
            {"path": str(candidate), "ok": True,
             "size": len(data_bytes), "name": candidate.name,
             "is_folder": False},
            data_bytes,
        )

    async def _h_file_list(self, data, payload):
        rel = data.get("path", "")
        share = config.shared_files_dir("client")
        target = Path(rel) if os.path.isabs(rel) else share / rel
        items: list[dict] = []
        try:
            for p in sorted(target.iterdir()):
                items.append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": p.stat().st_size if p.is_file() else 0,
                })
        except Exception as exc:
            await write_frame(
                self.writer, Op.FILE_LIST_RESULT,
                {"path": str(target), "ok": False, "reason": str(exc)},
            )
            return
        await write_frame(
            self.writer, Op.FILE_LIST_RESULT,
            {"path": str(target), "ok": True, "items": items},
        )

    # --------------------- Launch / blocking / power ----------------

    async def _h_launch(self, data, payload):
        rc = mac_launcher.open_target(data.get("target", ""))
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.LAUNCH, "rc": rc, "target": data.get("target", "")},
        )

    async def _h_set_blocked_apps(self, data, payload):
        ids = data.get("apps", [])
        mac_block.set_blocked_apps(ids)
        mac_block.start_watchdog()
        # Kill matching apps right now so the user gets immediate feedback.
        killed = mac_block._kill_blocked_now()
        if killed:
            LOG.info("blocked-app watchdog killed %d running app(s)", killed)
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.SET_BLOCKED_APPS, "count": len(ids), "killed_now": killed},
        )

    async def _h_get_running_apps(self, data, payload):
        apps = mac_block.list_running_apps()
        await write_frame(
            self.writer, Op.RUNNING_APPS,
            {"apps": apps, "count": len(apps)},
        )

    async def _h_kill_app(self, data, payload):
        pid = int(data.get("pid", 0))
        bundle_id = data.get("bundle_id", "")
        force = bool(data.get("force", True))
        result = mac_block.kill_app(pid=pid, bundle_id=bundle_id, force=force)
        LOG.info(
            "kill_app(pid=%s, bundle_id=%r, force=%s) -> %s",
            pid, bundle_id, force, result,
        )
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.KILL_APP,
             "ok": bool(result.get("ok", False)),
             "reason": result.get("reason", ""),
             "pid": pid, "bundle_id": bundle_id},
        )

    async def _h_set_blocked_urls(self, data, payload):
        urls = data.get("urls", [])
        result = mac_block.set_blocked_urls(urls)
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.SET_BLOCKED_URLS,
             "ok": bool(result.get("ok", False)),
             "reason": result.get("reason", ""),
             "applied": result.get("applied", []),
             "count": len(result.get("applied", []))},
        )

    async def _h_internet_lockdown(self, data, payload):
        master_ips = data.get("master_ips") or [self.peer_ip]
        result = mac_internet.enable_lockdown(master_ips)
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.INTERNET_LOCKDOWN,
             "ok": bool(result.get("ok", False)),
             "reason": result.get("reason", ""),
             "ips": master_ips},
        )

    async def _h_internet_release(self, data, payload):
        result = mac_internet.disable_lockdown()
        await write_frame(
            self.writer, Op.ACK,
            {"for": Op.INTERNET_RELEASE,
             "ok": bool(result.get("ok", False)),
             "reason": result.get("reason", "")},
        )

    async def _h_power(self, data, payload):
        action = data.get("action", "")
        fn = {
            "shutdown": mac_power.shutdown,
            "restart": mac_power.restart,
            "sleep": mac_power.sleep,
            "logout": mac_power.logout,
            "wake": mac_power.wake,
        }.get(action)
        if fn is None:
            await write_frame(self.writer, Op.ERROR, {"reason": f"unknown power action {action}"})
            return
        await write_frame(self.writer, Op.ACK, {"for": Op.POWER, "action": action})
        # Run the power command after the ACK has flushed so the master sees it.
        await asyncio.sleep(0.2)
        fn()

    async def _h_audio(self, data, payload):
        action = data.get("action", "")
        if action == "volume":
            mac_audio.set_volume(int(data.get("value", 50)))
        elif action == "mute":
            mac_audio.set_muted(True)
        elif action == "unmute":
            mac_audio.set_muted(False)
        await write_frame(self.writer, Op.ACK, {"for": Op.AUDIO, "action": action})


# ---------------------------------------------------------------------------
# Asyncio server
# ---------------------------------------------------------------------------


class DaemonServer:
    def __init__(self, port: int, key: bytes, ssl_ctx, overlay_ctrl):
        self.port = port
        self.key = key
        self.ssl_ctx = ssl_ctx
        self.overlay_ctrl = overlay_ctrl
        self._active: Optional[ClientSession] = None
        self._active_lock = asyncio.Lock()

    async def serve(self):
        server = await asyncio.start_server(
            self._on_connect, host="0.0.0.0", port=self.port, ssl=self.ssl_ctx,
        )
        LOG.info("listening on 0.0.0.0:%d (TLS)", self.port)
        async with server:
            await server.serve_forever()

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        LOG.info("incoming TLS connection from %s", peer)
        # Reject if a session is already active (single-master model, like Veyon).
        if self._active is not None:
            LOG.warning("rejecting %s: a session is already active", peer)
            try:
                await write_frame(writer, Op.ERROR, {"reason": "busy"})
            finally:
                writer.close()
                await writer.wait_closed()
            return

        ok = await server_authenticate(reader, writer, self.key)
        if not ok:
            LOG.warning("auth failed from %s", peer)
            writer.close()
            await writer.wait_closed()
            return

        session = ClientSession(reader, writer, self.overlay_ctrl)
        async with self._active_lock:
            self._active = session
        try:
            await session.run()
        finally:
            async with self._active_lock:
                self._active = None


def _run_asyncio(loop_started: threading.Event, port: int, overlay_ctrl):
    cert, key_file = config.cert_paths("client")
    ensure_self_signed(str(cert), str(key_file))
    key = load_or_create_key(str(config.key_path("client")))
    LOG.info("client auth key loaded (fingerprint: %s)", key_fingerprint(key))
    ssl_ctx = make_server_ssl(str(cert), str(key_file))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = DaemonServer(port, key, ssl_ctx, overlay_ctrl)
    mac_block.start_watchdog(loop)
    loop_started.set()
    try:
        loop.run_until_complete(server.serve())
    except Exception:
        LOG.exception("asyncio server crashed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassControl client daemon")
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--print-key", action="store_true",
                        help="Print the shared key (creating one if needed) and exit")
    args = parser.parse_args(argv)

    if args.print_key:
        k = load_or_create_key(str(config.key_path("client")))
        print(k.hex())
        return 0

    # Must happen BEFORE QApplication() so the macOS menu bar shows
    # "ClassControl Client" instead of "Python" when running from source.
    from shared.macos_app import set_app_name
    set_app_name("ClassControl Client")

    app = QApplication(sys.argv)
    app.setApplicationName("ClassControl Client")
    app.setApplicationDisplayName("ClassControl Client")
    app.setOrganizationName("ClassControl")
    app.setQuitOnLastWindowClosed(False)  # daemon: no main window
    overlay_ctrl = overlay.OverlayController()

    started = threading.Event()
    t = threading.Thread(
        target=_run_asyncio,
        args=(started, args.port, overlay_ctrl),
        name="classcontrol-asyncio",
        daemon=True,
    )
    t.start()
    started.wait(timeout=5)
    LOG.info("ClassControl client started on port %d", args.port)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
