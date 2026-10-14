"""Protected PKI files and durable directory synchronization."""

import json
import os
from pathlib import Path
import stat

from .errors import PkiError

MAX_FILE = 16 * 1024 * 1024


def read_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise PkiError("storage_error")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(MAX_FILE + 1)
    finally:
        os.close(fd)


def write_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path: Path, data: object) -> None:
    write_file(path, json.dumps(data, sort_keys=True, separators=(",", ":")).encode())


def load_json(path: Path):
    return json.loads(read_file(path))


def mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise PkiError("storage_error")
    os.chmod(path, 0o700)


def check_tree(path: Path, *, protected: bool = True) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (protected and stat.S_IMODE(info.st_mode) != 0o700):
        raise PkiError("storage_error")
    for child in path.iterdir():
        info = child.lstat()
        if stat.S_ISDIR(info.st_mode):
            check_tree(child, protected=protected)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
            raise PkiError("storage_error")
        elif protected and stat.S_IMODE(info.st_mode) != 0o600:
            raise PkiError("storage_error")


def sync_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            sync_tree(child)
        else:
            with child.open("rb") as stream:
                os.fsync(stream.fileno())
    sync_directory(path)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
