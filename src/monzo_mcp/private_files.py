"""Safe reads for small owner-only text secret files."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class PrivateTextFileError(Exception):
    """A private text file could not be read safely."""


def read_private_text_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> str:
    """Read one non-empty private regular file without following symlinks."""
    if not path.is_absolute():
        raise PrivateTextFileError(f"{label} path must be absolute")
    try:
        metadata = path.lstat()
    except OSError:
        raise PrivateTextFileError(f"{label} could not be read") from None
    if not _is_private_regular_file(metadata, max_bytes=max_bytes):
        raise PrivateTextFileError(f"{label} must be a private regular file")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened_metadata = os.fstat(stream.fileno())
            if not _is_private_regular_file(
                opened_metadata,
                max_bytes=max_bytes,
            ):
                raise PrivateTextFileError(f"{label} must be a private regular file")
            value = stream.read(max_bytes + 1).decode().strip()
    except PrivateTextFileError:
        raise
    except (OSError, UnicodeDecodeError):
        raise PrivateTextFileError(f"{label} could not be read") from None
    if not value:
        raise PrivateTextFileError(f"{label} must not be empty")
    return value


def _is_private_regular_file(
    metadata: os.stat_result,
    *,
    max_bytes: int,
) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        and metadata.st_size <= max_bytes
    )
