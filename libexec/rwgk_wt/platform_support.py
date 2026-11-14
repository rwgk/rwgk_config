"""Platform-specific locking and persistence for local recovery operations."""

from __future__ import annotations

import errno
import os

IS_WINDOWS = os.name == "nt"


def lock_exclusive(stream):
    """Acquire a nonblocking exclusive lock without changing the lock file."""
    if IS_WINDOWS:
        import msvcrt

        # Windows permits locking beyond EOF, including an empty lock file.
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno == errno.EACCES:
                raise BlockingIOError(*error.args) from error
            raise
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(stream):
    if IS_WINDOWS:
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_UN)


def fsync_directory(path):
    # Native Windows cannot open a directory through os.open for fsync.
    if IS_WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
