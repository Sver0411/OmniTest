"""Enforce a single scheduler per SQLite data directory."""

import os


class WorkerLock:
    def __init__(self, directory):
        self.path = directory / "worker.lock"
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError(
                "Data directory is already in use. Use CLI --url to access the running server."
            ) from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
