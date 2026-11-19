"""One companion owner per physical AddOns directory, using native OS locks.

The lock file is persistent: unlinking it would let another process lock a new
inode while an existing owner still holds the old one. It contains no identity
or credentials (only a NUL byte on Windows for the byte-range lock).
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import sys


class AlreadyRunningError(RuntimeError):
    """Another companion owns the same AddOns directory."""


class InstanceLock:
    """Nonblocking lifetime lock; use as a context manager or acquire/close pair."""

    def __init__(self, addons: str | Path):
        self.path = Path(addons).resolve() / ".hermeswow.lock"
        self._fd = None
        self._unlock = None

    def acquire(self):
        if self._fd is not None:
            return self
        # Do not truncate, unlink or create the AddOns directory. A missing or
        # unwritable installation is an IO/configuration error, not contention.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

                def unlock():
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                def unlock():
                    fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise AlreadyRunningError(
                    f"HermesWoW companion already running for AddOns at {self.path.parent}"
                ) from exc
            raise
        except BaseException:
            os.close(fd)
            raise
        self._fd, self._unlock = fd, unlock
        return self

    def close(self):
        if self._fd is None:
            return
        fd, unlock = self._fd, self._unlock
        self._fd = self._unlock = None
        try:
            assert unlock is not None
            unlock()
        finally:
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.close()
