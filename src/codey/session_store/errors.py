"""Errors raised by session_store."""
from __future__ import annotations


class SessionResumeError(Exception):
    """Raised when a session cannot be safely resumed (workspace gone,
    profile gone, corrupted jsonl, missing meta)."""
