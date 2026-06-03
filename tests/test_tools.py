"""Tests for the file-system / search / edit tools.

Tools themselves are now pure capability functions — permission gating has
moved to the PreToolUse permission hook. Tests that used to drive permission
through the tool now drive it through the hook directly (see test_hooks.py for
broader hook tests; test_permissions.py for engine logic).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codey.tools.apply_edit import ApplyEditTool
from codey.tools.grep import GrepTool
from codey.tools.list_dir import ListDirTool
from codey.tools.read_file import ReadFileTool
from codey.tools.write_file import WriteFileTool


# ---------- helpers ----------

def _run_perm_hook(engine, approve, tool: str, args: dict):
    """Invoke the permission hook directly, returning the HookResult."""
    import asyncio
    from codey.builtin_hooks.permission import permission_check_hook
    hook = permission_check_hook(engine=engine, approve=approve)
    payload = {"tool": tool, "arguments": args, "call_id": "test"}
    result = asyncio.get_event_loop().run_until_complete(hook(payload)) \
        if False else None
    # Use asyncio.run safely from a sync helper if not in a loop:
    return asyncio.run(hook(payload))


# ---------- read_file ----------

async def test_read_file_returns_contents(tmp_path: Path):
    p = tmp_path / "hello.txt"
    p.write_text("hi there\nsecond line\n", encoding="utf-8")
    out = await ReadFileTool().run({"path": str(p)})
    assert out == "hi there\nsecond line\n"


# ---------- read_file workspace gating (now via permission hook) ----------

async def test_read_file_outside_workspace_asks(tmp_path: Path):
    """The permission hook must consult approve for an outside-workspace read."""
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import PermissionEngine, Mode
    inside = tmp_path / "ws"
    outside = tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("password=hunter2")

    eng = PermissionEngine(mode=Mode.SAFE, workspace=inside.resolve())
    seen = []
    def approve(ctx):
        seen.append(ctx)
        return False  # deny
    hook = permission_check_hook(engine=eng, approve=approve)
    result = await hook({"tool": "read_file",
                         "arguments": {"path": str(target)},
                         "call_id": "x"})
    assert len(seen) == 1, f"expected approve to be consulted, got {seen}"
    assert seen[0]["tool"] == "read_file"
    assert "outside the workspace" in (seen[0].get("reason") or "")
    assert result is not None and result.cancel
    assert result.result.startswith("error: user denied")


async def test_read_file_inside_workspace_skips_approval(tmp_path: Path):
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import PermissionEngine, Mode
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "open.txt"
    target.write_text("ok")
    eng = PermissionEngine(mode=Mode.SAFE, workspace=ws.resolve())
    seen = []
    hook = permission_check_hook(
        engine=eng, approve=lambda ctx: (seen.append(ctx), True)[1])
    result = await hook({"tool": "read_file",
                         "arguments": {"path": str(target)},
                         "call_id": "x"})
    assert result is None  # allow (None = proceed)
    assert seen == []


async def test_read_file_yolo_bypasses_workspace(tmp_path: Path):
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import PermissionEngine, Mode
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "anything.txt"
    outside.write_text("nope")
    eng = PermissionEngine(mode=Mode.YOLO, workspace=ws.resolve())
    seen = []
    hook = permission_check_hook(
        engine=eng, approve=lambda ctx: (seen.append(ctx), True)[1])
    result = await hook({"tool": "read_file",
                         "arguments": {"path": str(outside)},
                         "call_id": "x"})
    assert result is None
    assert seen == []


async def test_list_dir_outside_workspace_asks(tmp_path: Path):
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import PermissionEngine, Mode
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    ws.mkdir(); other.mkdir()
    eng = PermissionEngine(mode=Mode.SAFE, workspace=ws.resolve())
    seen = []
    hook = permission_check_hook(
        engine=eng, approve=lambda ctx: (seen.append(ctx), False)[1])
    result = await hook({"tool": "list_dir",
                         "arguments": {"path": str(other)},
                         "call_id": "x"})
    assert len(seen) == 1
    assert "outside the workspace" in (seen[0].get("reason") or "")
    assert result is not None and result.cancel


async def test_grep_outside_workspace_asks(tmp_path: Path):
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import PermissionEngine, Mode
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    ws.mkdir(); other.mkdir()
    eng = PermissionEngine(mode=Mode.SAFE, workspace=ws.resolve())
    seen = []
    hook = permission_check_hook(
        engine=eng, approve=lambda ctx: (seen.append(ctx), False)[1])
    result = await hook({"tool": "grep",
                         "arguments": {"pattern": "x", "path": str(other)},
                         "call_id": "x"})
    assert len(seen) == 1
    assert seen[0]["tool"] == "grep"
    assert "outside the workspace" in (seen[0].get("reason") or "")
    assert result is not None and result.cancel


async def test_read_file_missing(tmp_path: Path):
    out = await ReadFileTool().run({"path": str(tmp_path / "nope.txt")})
    assert out.startswith("error: file not found")


async def test_read_file_directory(tmp_path: Path):
    out = await ReadFileTool().run({"path": str(tmp_path)})
    assert out.startswith("error:")
    assert "directory" in out


async def test_read_file_binary(tmp_path: Path):
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\xff\xfe\x00\x01garbage")
    out = await ReadFileTool().run({"path": str(p)})
    assert out.startswith("error:")
    assert "UTF-8" in out


async def test_read_file_too_large(tmp_path: Path):
    from codey.tools.read_file import MAX_BYTES
    p = tmp_path / "big.txt"
    p.write_bytes(b"x" * (MAX_BYTES + 1))
    out = await ReadFileTool().run({"path": str(p)})
    assert "max" in out and "bytes" in out


# ---------- list_dir ----------

async def test_list_dir_basic(tmp_path: Path):
    (tmp_path / "a.txt").write_text("aa")
    (tmp_path / "b.txt").write_text("bbb")
    (tmp_path / "sub").mkdir()
    out = await ListDirTool().run({"path": str(tmp_path)})
    lines = out.splitlines()
    names = [line.split()[-1] for line in lines]
    assert names == ["a.txt", "b.txt", "sub"]
    # sub is dir, a.txt is file with size 2
    assert "dir" in [line.split()[0] for line in lines]
    assert any("a.txt" in line and "2" in line.split() for line in lines)


async def test_list_dir_skips_hidden_by_default(tmp_path: Path):
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "visible.txt").write_text("y")
    out = await ListDirTool().run({"path": str(tmp_path)})
    assert ".secret" not in out
    assert "visible.txt" in out


async def test_list_dir_show_hidden(tmp_path: Path):
    (tmp_path / ".secret").write_text("x")
    out = await ListDirTool().run({"path": str(tmp_path), "show_hidden": True})
    assert ".secret" in out


async def test_list_dir_not_found(tmp_path: Path):
    out = await ListDirTool().run({"path": str(tmp_path / "nope")})
    assert out.startswith("error: directory not found")


async def test_list_dir_empty(tmp_path: Path):
    out = await ListDirTool().run({"path": str(tmp_path)})
    assert out.startswith("(empty:")


# ---------- grep ----------

async def test_grep_finds_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("class Bar:\n    def foo(self): pass\n")
    out = await GrepTool().run({"pattern": r"def foo", "path": str(tmp_path)})
    assert "a.py" in out
    assert "b.py" in out
    assert "def foo" in out


async def test_grep_no_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("nothing here\n")
    out = await GrepTool().run({"pattern": "zzz", "path": str(tmp_path)})
    assert out == "(no matches)"


async def test_grep_case_insensitive(tmp_path: Path):
    (tmp_path / "a.txt").write_text("Hello World\n")
    out = await GrepTool().run({
        "pattern": "hello", "path": str(tmp_path), "case_insensitive": True
    })
    assert "Hello World" in out


async def test_grep_glob_filter(tmp_path: Path):
    (tmp_path / "a.py").write_text("match me\n")
    (tmp_path / "a.txt").write_text("match me\n")
    out = await GrepTool().run({
        "pattern": "match", "path": str(tmp_path), "glob": "*.py"
    })
    assert "a.py" in out
    assert "a.txt" not in out


async def test_grep_invalid_regex(tmp_path: Path):
    out = await GrepTool().run({"pattern": "(", "path": str(tmp_path)})
    assert out.startswith("error: invalid regex")


async def test_grep_skips_skip_dirs(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("secret_value\n")
    (tmp_path / "shown.py").write_text("secret_value\n")
    out = await GrepTool().run({"pattern": "secret_value", "path": str(tmp_path)})
    assert "shown.py" in out
    assert ".git" not in out


# ---------- write_file ----------

async def test_write_file_creates(tmp_path: Path):
    target = tmp_path / "sub" / "new.txt"
    out = await WriteFileTool().run({"path": str(target), "content": "hello"})
    assert out.startswith("ok:")
    assert target.read_text() == "hello"


async def test_write_file_overwrites(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("old")
    out = await WriteFileTool().run({"path": str(target), "content": "new"})
    assert out.startswith("ok:")
    assert target.read_text() == "new"


# Note: approval/permission behavior for write_file is now tested in
# test_permissions.py and test_hooks.py through the PreToolUse hook.


# ---------- apply_edit ----------

_EDIT = (
    "<<<<<<< SEARCH\n"
    "{old}\n"
    "=======\n"
    "{new}\n"
    ">>>>>>> REPLACE"
)


async def test_apply_edit_single_block(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("line one\nline two\nline three\n")
    edits = _EDIT.format(old="line two", new="LINE TWO")
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("ok:")
    assert target.read_text() == "line one\nLINE TWO\nline three\n"


async def test_apply_edit_multiple_blocks(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    edits = (
        _EDIT.format(old="alpha", new="ALPHA")
        + "\n"
        + _EDIT.format(old="gamma", new="GAMMA")
    )
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("ok:")
    assert target.read_text() == "ALPHA\nbeta\nGAMMA\n"


async def test_apply_edit_search_not_found_atomic(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("original\n")
    edits = _EDIT.format(old="nonexistent", new="x")
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("error:")
    assert "not found" in out
    # File untouched.
    assert target.read_text() == "original\n"


async def test_apply_edit_ambiguous_match(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("dup\ndup\n")
    edits = _EDIT.format(old="dup", new="DUP")
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("error:")
    assert "matches" in out
    assert target.read_text() == "dup\ndup\n"


async def test_apply_edit_no_blocks(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    out = await ApplyEditTool().run({"path": str(target), "edits": "just text, no blocks"})
    assert out.startswith("error: no SEARCH/REPLACE blocks")


async def test_apply_edit_creates_new_file_with_empty_search(tmp_path: Path):
    target = tmp_path / "newfile.py"
    edits = _EDIT.format(old="", new="print('hi')\n")
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("ok: created")
    assert target.read_text() == "print('hi')\n"


async def test_apply_edit_create_refuses_when_exists(tmp_path: Path):
    target = tmp_path / "exists.py"
    target.write_text("already here\n")
    edits = _EDIT.format(old="", new="overwrite\n")
    out = await ApplyEditTool().run({"path": str(target), "edits": edits})
    assert out.startswith("error:")
    assert "exists" in out
    assert target.read_text() == "already here\n"


# Note: apply_edit approval behavior is now covered in test_hooks.py via the
# PreToolUse permission hook.


async def test_apply_edit_missing_file(tmp_path: Path):
    edits = _EDIT.format(old="x", new="y")
    out = await ApplyEditTool().run({
        "path": str(tmp_path / "nope.txt"), "edits": edits
    })
    assert out.startswith("error: file not found")


# ---------- registry composition smoke ----------

async def test_default_registry_contains_all_tools():
    from codey.tools import build_default_registry
    reg = build_default_registry()
    names = set(reg.tools)
    assert names == {"bash", "read_file", "list_dir", "grep", "write_file", "apply_edit"}
    # Each tool has a non-empty schema.
    for schema in reg.schemas():
        assert schema["function"]["name"]
        assert schema["function"]["description"]
        assert "parameters" in schema["function"]
