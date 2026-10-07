from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import threading

from .core import PkiError, Store

MAX_REQUEST = 4096
MAX_RESPONSE = 131072


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def receive(connection: socket.socket, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        part = connection.recv(size - len(value))
        if not part:
            raise ValueError("short message")
        value.extend(part)
    return bytes(value)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        connection = self.request
        connection.settimeout(10)
        _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        if uid != self.server.allowed_uid:
            return
        try:
            length = struct.unpack("!I", receive(connection, 4))[0]
            if not 1 <= length <= MAX_REQUEST:
                raise PkiError("invalid_request")
            request = json.loads(receive(connection, length), object_pairs_hook=unique_object)
            response = {"ok": True, "result": self.server.store.dispatch(request)}
        except PkiError as error:
            response = {"ok": False, "error": error.code}
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            response = {"ok": False, "error": "invalid_request"}
        except Exception:
            # No exception repr/traceback: paths, provider output and material are private.
            response = {"ok": False, "error": "operation_failed"}
        try:
            encoded = json.dumps(response, separators=(",", ":")).encode()
            if len(encoded) > MAX_RESPONSE:
                encoded = b'{"ok":false,"error":"operation_failed"}'
            connection.sendall(struct.pack("!I", len(encoded)) + encoded)
        except OSError:
            pass  # A dropped response is retried with the same job identifier.


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 8

    def __init__(self, path: Path, store: Store, allowed_uid: int = 10001, socket_gid: int = 10003):
        self.store = store
        self.allowed_uid = allowed_uid
        self.slots = threading.BoundedSemaphore(8)
        self.socket_gid = socket_gid
        self.path = path
        # One live server per storage, so no live socket is unlinked on restart.
        import fcntl
        self.lock_fd = os.open(store.root / "serve.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with store.locked():
                store.recover()
                store.current()  # Serving uninitialized storage fails closed.
            if path.exists() or path.is_symlink():
                if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != os.geteuid():
                    raise PkiError("storage_error")
                path.unlink()
            super().__init__(str(path), Handler)
        except Exception:
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None
            raise

    def server_bind(self):
        super().server_bind()
        os.chown(self.path, -1, self.socket_gid)
        os.chmod(self.path, 0o660)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def server_close(self):
        super().server_close()
        self.path.unlink(missing_ok=True)
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
