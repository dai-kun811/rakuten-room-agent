from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RoomProfileLockTimeout(RuntimeError):
    pass


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def room_profile_lock(
    path: Path,
    *,
    timeout_seconds: float = 110 * 60,
    poll_seconds: float = 1.0,
) -> Iterator[None]:
    """Serialize Playwright processes that share the ROOM Chrome profile."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()

    deadline = time.monotonic() + max(timeout_seconds, 0)
    acquired = False
    try:
        while True:
            try:
                _lock_file(handle)
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RoomProfileLockTimeout(
                        "Timed out waiting for the shared ROOM Chrome profile."
                    ) from exc
                time.sleep(max(poll_seconds, 0.05))
        yield
    finally:
        if acquired:
            _unlock_file(handle)
        handle.close()
