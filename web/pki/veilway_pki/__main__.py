"""Fixed service command or explicit, offline operator import."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

from .core import PkiError, Store, read_file
from .server import Server


def secret(path: str) -> bytes:
    if path.startswith("/proc/self/fd/") and path.rsplit("/", 1)[1].isdigit():
        return os.pread(int(path.rsplit("/", 1)[1]), 65536, 0)
    return read_file(Path(path))


def main():
    parser = argparse.ArgumentParser(prog="veilway-pki-service")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    importer = sub.add_parser("import-ca")
    importer.add_argument("--source", type=Path, required=True)
    profiles = sub.add_parser("import-profiles")
    profiles.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        store = Store(Path("/var/lib/veilway-pki"), secret(os.environ.get("VEILWAY_PKI_PASSPHRASE_FILE", "/run/secrets/pki_ca_passphrase")))
        if args.command == "import-ca":
            endpoints = json.loads(secret(os.environ.get("VEILWAY_PKI_ENDPOINTS_FILE", "/run/secrets/pki_endpoints")))
            store.import_ca(args.source, endpoints)
            print("PKI import committed. No legacy profiles were imported.")
        elif args.command == "import-profiles":
            from .legacy import import_profiles
            imported = import_profiles(store, args.source)
            print(f"Legacy profile import committed: {len(imported)} profiles. Synchronize panel metadata separately.")
        else:
            with Server(Path("/run/veilway-pki/pki.sock"), store) as server:
                def stop(_signum, _frame):
                    # shutdown must run outside serve_forever's thread, including PID 1.
                    threading.Thread(target=server.shutdown, daemon=True).start()
                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                print("PKI ready.", flush=True)
                server.serve_forever(poll_interval=0.5)
    except PkiError as error:
        print(f"PKI failed: {error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("PKI failed: unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
