"""Small owner-only filesystem primitives shared by sensitive exporters."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


def ensure_private_directory(path: str | Path) -> Path:
    """Create or tighten a real directory, refusing a symlink at the leaf."""

    directory = Path(path)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"private output path is not a real directory: {directory}")
    os.chmod(directory, 0o700)
    return directory


@contextmanager
def open_new_private(path: str | Path) -> Iterator[BinaryIO]:
    """Create a new regular file as mode 0600 without following symlinks."""

    destination = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    complete = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"private output is not a regular file: {destination}")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            yield output
            output.flush()
            os.fsync(output.fileno())
        complete = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not complete:
            destination.unlink(missing_ok=True)


__all__ = ["ensure_private_directory", "open_new_private"]
