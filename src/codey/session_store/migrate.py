"""One-time migration: rename the legacy `profile` key to `provider` in every
session `meta.json`.

Sessions created before the profile→provider rename have sidecars with a
`"profile"` key and no `"provider"` key. `SessionMeta.load` requires
`provider`, so those sessions fail to resume with `KeyError: 'provider'`.
This rewrites each affected sidecar in place, preserving every other key.

Run via the `codey-migrate-sessions` console script (see pyproject.toml).
Idempotent: sidecars that already have `provider` are left untouched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .store import _default_root


@dataclass
class MigrationResult:
    migrated: list[str]  # session ids rewritten
    skipped: list[str]   # already had provider / nothing to do
    failed: list[tuple[str, str]]  # (session id, error message)


def migrate_meta_file(meta_path: Path) -> str:
    """Migrate one meta.json. Returns "migrated", "skipped", or raises.

    Renames `profile` → `provider` only when `provider` is absent. All other
    keys (including unknown ones) are preserved and key order is otherwise
    kept stable.
    """
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if "provider" in data:
        return "skipped"
    if "profile" not in data:
        return "skipped"

    migrated = {}
    for key, value in data.items():
        if key == "profile":
            migrated["provider"] = value
        else:
            migrated[key] = value

    meta_path.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        meta_path.chmod(0o600)
    except OSError:
        pass
    return "migrated"


def migrate_all(root: Path | None = None) -> MigrationResult:
    """Walk every `<sid>/meta.json` under `root` and migrate legacy sidecars."""
    root = root if root is not None else _default_root()
    result = MigrationResult(migrated=[], skipped=[], failed=[])
    if not root.is_dir():
        return result

    for child in sorted(root.iterdir()):
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        sid = child.name
        try:
            outcome = migrate_meta_file(meta_path)
        except (OSError, json.JSONDecodeError) as e:
            result.failed.append((sid, str(e)))
            continue
        (result.migrated if outcome == "migrated" else result.skipped).append(sid)
    return result


def main() -> None:
    """Console entry point: migrate ~/.cache/codey/transcripts and report."""
    result = migrate_all()
    print(f"migrated {len(result.migrated)} session(s), "
          f"skipped {len(result.skipped)}, failed {len(result.failed)}")
    for sid in result.migrated:
        print(f"  migrated {sid}")
    for sid, err in result.failed:
        print(f"  FAILED   {sid}: {err}")


if __name__ == "__main__":
    main()
