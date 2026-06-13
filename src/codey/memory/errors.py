"""Errors raised by codey.memory."""
from __future__ import annotations


class MemoryError(Exception):
    """Base class for memory-package failures."""


class MemoryWriteError(MemoryError):
    pass


class MemoryParseError(MemoryError):
    pass
