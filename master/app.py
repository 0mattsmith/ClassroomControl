"""ClassControl teacher app entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python -m master.app` from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

from shared import config, logging_setup, protocol
from shared.protocol import load_or_create_key, key_fingerprint
from master.connection import ConnectionHub
from master.roster import Roster
from master.ui.main_window import MainWindow


LOG = logging_setup.configure(
    "classcontrol.master",
    config.user_config_dir("master") / "master.log",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassControl teacher app")
    parser.add_argument("--print-key", action="store_true",
                        help="Print the master's shared key (creating one if needed)")
    parser.add_argument("--set-key",
                        help="Replace the shared key with the given hex string")
    args = parser.parse_args(argv)

    key_path = config.key_path("master")

    if args.set_key:
        try:
            bytes.fromhex(args.set_key)
        except ValueError:
            print("--set-key must be a hex string", file=sys.stderr)
            return 2
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(args.set_key.strip())
        print(f"shared key written to {key_path}")
        return 0

    if args.print_key:
        key = load_or_create_key(str(key_path))
        print(key.hex())
        return 0

    # Ensure key exists before launching UI, and log its fingerprint so
    # an operator can compare with the client's startup log to verify
    # they're paired correctly.
    _k = load_or_create_key(str(key_path))
    LOG.info("master auth key loaded (fingerprint: %s)", key_fingerprint(_k))

    # Must happen BEFORE QApplication() so the macOS menu bar shows
    # "ClassControl Teacher" instead of "Python" when running from source.
    from shared.macos_app import set_app_name
    set_app_name("ClassControl Teacher")

    app = QApplication(sys.argv)
    app.setApplicationName("ClassControl Teacher")
    app.setApplicationDisplayName("ClassControl Teacher")
    app.setOrganizationName("ClassControl")
    app.setOrganizationDomain("classcontrol.local")

    hub = ConnectionHub()
    hub.start()

    roster = Roster.load()
    win = MainWindow(hub, roster)
    win.show()

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
