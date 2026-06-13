"""Memory dataclass and Scope literal."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Scope = Literal["global", "project"]


@dataclass(frozen=True)
class Memory:
    name: str
    description: str
    type: str
    body: str
    created_at: str
    updated_at: str
    source_session: str
    scope: Scope
    source_path: Path
