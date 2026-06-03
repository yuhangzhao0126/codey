"""Tests for the permission engine: matching, modes, rule files, suggestions."""

from __future__ import annotations

from pathlib import Path

import pytest

from codey.permissions import (
    Allow,
    Ask,
    Deny,
    Mode,
    PermissionEngine,
    Rule,
    suggest_pattern,
)


# ---------- pattern matching ----------

def test_check_builtin_deny_rm_rf():
    eng = PermissionEngine(mode=Mode.SAFE)
    d = eng.check("bash", "rm -rf /")
    assert isinstance(d, Deny)
    assert "destructive" in d.reason.lower()


def test_check_builtin_allow_ls():
    eng = PermissionEngine(mode=Mode.SAFE)
    d = eng.check("bash", "ls -la")
    assert isinstance(d, Allow)


def test_check_unknown_bash_asks():
    eng = PermissionEngine(mode=Mode.SAFE)
    d = eng.check("bash", "npm install")
    assert isinstance(d, Ask)


def test_check_writer_default_asks():
    eng = PermissionEngine(mode=Mode.SAFE)
    d = eng.check("write_file", "/tmp/foo.txt")
    assert isinstance(d, Ask)


def test_check_reader_auto_allow():
    eng = PermissionEngine(mode=Mode.SAFE)
    d = eng.check("read_file", "/tmp/foo.txt")
    assert isinstance(d, Allow)


# ---------- modes ----------

def test_yolo_allows_unknown_but_still_denies_builtin():
    eng = PermissionEngine(mode=Mode.YOLO)
    assert isinstance(eng.check("bash", "rm -rf /"), Deny)
    assert isinstance(eng.check("bash", "anything else"), Allow)
    assert isinstance(eng.check("write_file", "/tmp/foo"), Allow)


def test_paranoid_asks_everything_except_builtin_deny():
    eng = PermissionEngine(mode=Mode.PARANOID)
    assert isinstance(eng.check("bash", "rm -rf /"), Deny)
    assert isinstance(eng.check("bash", "ls"), Ask)
    assert isinstance(eng.check("read_file", "/tmp/foo"), Ask)


def test_read_only_allows_readers_asks_writers():
    eng = PermissionEngine(mode=Mode.READ_ONLY)
    assert isinstance(eng.check("read_file", "/tmp/foo"), Allow)
    assert isinstance(eng.check("list_dir", "/tmp"), Allow)
    assert isinstance(eng.check("write_file", "/tmp/foo"), Ask)
    assert isinstance(eng.check("apply_edit", "/tmp/foo"), Ask)
    # Bash: allowlisted command runs; unknown command asks.
    assert isinstance(eng.check("bash", "ls"), Allow)
    assert isinstance(eng.check("bash", "rm /tmp/foo"), Ask)


# ---------- user / project rules ----------

def test_user_allow_rule_promotes_default_ask():
    eng = PermissionEngine(
        mode=Mode.SAFE,
        user_rules=[Rule("bash", "npm test*", "allow", "trust npm test")],
    )
    assert isinstance(eng.check("bash", "npm test"), Allow)
    assert isinstance(eng.check("bash", "npm test --watch"), Allow)
    assert isinstance(eng.check("bash", "npm install"), Ask)


def test_project_deny_rule_blocks_command():
    eng = PermissionEngine(
        mode=Mode.SAFE,
        project_rules=[Rule("bash", "curl *", "deny", "no network in this repo")],
    )
    d = eng.check("bash", "curl https://example.com")
    assert isinstance(d, Deny)
    assert "no network" in d.reason


def test_user_allow_cannot_override_builtin_deny():
    eng = PermissionEngine(
        mode=Mode.SAFE,
        user_rules=[Rule("bash", "rm -rf *", "allow", "I know what I'm doing")],
    )
    assert isinstance(eng.check("bash", "rm -rf /tmp/scratch"), Deny)


def test_yolo_overrides_user_deny_but_not_builtin_deny():
    eng = PermissionEngine(
        mode=Mode.YOLO,
        user_rules=[Rule("bash", "ls *", "deny", "no listing")],
    )
    # YOLO short-circuits to Allow after built-in deny check.
    assert isinstance(eng.check("bash", "ls /tmp"), Allow)
    assert isinstance(eng.check("bash", "rm -rf /"), Deny)


def test_project_allow_beats_user_allow_order():
    # Project rules are checked before user rules for allows.
    eng = PermissionEngine(
        mode=Mode.SAFE,
        project_rules=[Rule("bash", "make *", "allow", "project make is fine")],
        user_rules=[Rule("bash", "make *", "deny", "global block")],
    )
    # Deny rules win in their own pass; user deny comes from user_rules deny pass
    # before allow pass — so this is still Deny.
    d = eng.check("bash", "make build")
    assert isinstance(d, Deny)


# ---------- TOML I/O round-trip ----------

def test_load_user_file_picks_up_mode_and_rules(tmp_path: Path):
    user = tmp_path / "permissions.toml"
    user.write_text(
        'mode = "yolo"\n'
        "\n[[rules]]\n"
        'tool = "bash"\n'
        'pattern = "npm test*"\n'
        'action = "allow"\n'
        'reason = "trust"\n'
    )
    eng = PermissionEngine.load(user_path=user, project_path=tmp_path / "missing.toml")
    assert eng.mode == Mode.YOLO
    assert len(eng.user_rules) == 1
    assert eng.user_rules[0].tool == "bash"
    assert eng.user_rules[0].action == "allow"


def test_missing_files_yield_defaults(tmp_path: Path):
    eng = PermissionEngine.load(
        user_path=tmp_path / "nope1.toml",
        project_path=tmp_path / "nope2.toml",
    )
    assert eng.mode == Mode.SAFE
    assert eng.user_rules == []
    assert eng.project_rules == []


def test_save_mode_persists(tmp_path: Path):
    path = tmp_path / "permissions.toml"
    eng = PermissionEngine(mode=Mode.SAFE)
    eng.save_mode(Mode.READ_ONLY, user_path=path)
    text = path.read_text()
    assert 'mode = "read-only"' in text
    # Reload to confirm round-trip.
    eng2 = PermissionEngine.load(user_path=path, project_path=tmp_path / "_proj.toml")
    assert eng2.mode == Mode.READ_ONLY


def test_append_user_rule_persists(tmp_path: Path):
    path = tmp_path / "permissions.toml"
    eng = PermissionEngine(mode=Mode.SAFE)
    eng.append_user_rule(
        Rule("bash", "git pull*", "allow", "trust git pull"),
        user_path=path,
    )
    text = path.read_text()
    assert "git pull*" in text
    assert "allow" in text
    eng2 = PermissionEngine.load(user_path=path, project_path=tmp_path / "_proj.toml")
    assert len(eng2.user_rules) == 1
    assert eng2.user_rules[0].pattern == "git pull*"


def test_append_project_rule_writes_no_mode(tmp_path: Path):
    path = tmp_path / "permissions.toml"
    eng = PermissionEngine(mode=Mode.SAFE)
    eng.append_project_rule(
        Rule("apply_edit", "/repo/*", "allow"),
        project_path=path,
    )
    text = path.read_text()
    assert "mode" not in text   # project files don't carry mode
    assert "/repo/*" in text


def test_remove_user_rule_at_index(tmp_path: Path):
    path = tmp_path / "permissions.toml"
    eng = PermissionEngine(mode=Mode.SAFE, user_rules=[
        Rule("bash", "a*", "allow"),
        Rule("bash", "b*", "allow"),
    ])
    removed = eng.remove_user_rule_at(0, user_path=path)
    assert removed is not None
    assert removed.pattern == "a*"
    assert len(eng.user_rules) == 1
    assert eng.user_rules[0].pattern == "b*"


# ---------- suggest_pattern ----------

def test_suggest_pattern_bash_two_tokens():
    assert suggest_pattern("bash", "git status -sb") == "git status*"


def test_suggest_pattern_bash_one_token():
    assert suggest_pattern("bash", "ls") == "ls*"


def test_suggest_pattern_file_tool_uses_parent_glob(tmp_path: Path):
    p = str(tmp_path / "x" / "f.txt")
    s = suggest_pattern("write_file", p)
    assert s.endswith("/*")
    assert "/x" in s


# ---------- path normalization for file tools (regression) ----------

def test_rule_with_expanded_path_matches_tilde_arg():
    """Rule saved as `/Users/me/Desktop/*` must match the model's `~/Desktop/foo`
    (the bug that made approved rules look like they were ignored)."""
    home = str(Path("~").expanduser())
    eng = PermissionEngine(
        mode=Mode.SAFE,
        project_rules=[Rule("apply_edit", f"{home}/Desktop/*", "allow", "ok")],
    )
    d = eng.check("apply_edit", "~/Desktop/hello.py")
    assert isinstance(d, Allow), d


def test_rule_with_tilde_pattern_matches_expanded_arg():
    """Symmetry: a rule saved as `~/Desktop/*` should also match an arg that
    arrives already expanded."""
    home = str(Path("~").expanduser())
    eng = PermissionEngine(
        mode=Mode.SAFE,
        user_rules=[Rule("write_file", "~/Desktop/*", "allow", "ok")],
    )
    d = eng.check("write_file", f"{home}/Desktop/note.txt")
    assert isinstance(d, Allow), d


def test_path_normalization_does_not_affect_bash():
    """`~` expansion is path-tool only; bash commands still match literally
    so we don't surprise the user by silently expanding tildes in commands.
    Use a command (`make`) that isn't in any built-in rule so the only
    possible match path is the user rule."""
    eng = PermissionEngine(
        mode=Mode.SAFE,
        user_rules=[Rule("bash", "make ~/foo", "allow")],
    )
    # Same literal string matches.
    assert isinstance(eng.check("bash", "make ~/foo"), Allow)
    # Expanded form does NOT match (it's a different command from bash's POV).
    home = str(Path("~").expanduser())
    d = eng.check("bash", f"make {home}/foo")
    assert isinstance(d, Ask), d


# ---------- workspace trust boundary ----------

def test_workspace_inside_path_allowed(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x")
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    d = eng.check("read_file", str(tmp_path / "f.txt"))
    assert isinstance(d, Allow), d


def test_workspace_outside_read_asks(tmp_path: Path):
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    d = eng.check("read_file", "/etc/hosts")
    assert isinstance(d, Ask)
    assert "outside the workspace" in d.reason


def test_workspace_outside_write_asks(tmp_path: Path):
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    d = eng.check("write_file", "/etc/something")
    assert isinstance(d, Ask)
    assert "outside the workspace" in d.reason


def test_workspace_outside_list_dir_asks(tmp_path: Path):
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    d = eng.check("list_dir", "/etc")
    assert isinstance(d, Ask)


def test_workspace_yolo_bypasses_boundary(tmp_path: Path):
    eng = PermissionEngine(mode=Mode.YOLO, workspace=tmp_path)
    assert isinstance(eng.check("read_file", "/etc/hosts"), Allow)
    assert isinstance(eng.check("write_file", "/etc/foo"), Allow)


def test_workspace_user_allow_rule_overrides_boundary(tmp_path: Path):
    """User can grant 'always allow' via the approval modal even for paths
    outside the workspace — that's the whole point of the 4-option UX."""
    eng = PermissionEngine(
        mode=Mode.SAFE,
        workspace=tmp_path,
        user_rules=[Rule("read_file", "/etc/*", "allow", "trusted")],
    )
    assert isinstance(eng.check("read_file", "/etc/hosts"), Allow)


def test_workspace_symlink_pointing_outside_treated_as_outside(tmp_path: Path):
    """A symlink inside the workspace that targets /etc should NOT slip past
    the boundary — we resolve symlinks before checking."""
    outside = tmp_path / "outside_root"
    outside.mkdir()
    (outside / "secret").write_text("hush")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "back_door"
    link.symlink_to(outside)

    eng = PermissionEngine(mode=Mode.SAFE, workspace=workspace)
    d = eng.check("read_file", str(link / "secret"))
    assert isinstance(d, Ask), d
    assert "outside" in d.reason


def test_workspace_none_means_no_boundary(tmp_path: Path):
    """Engines built without a workspace (e.g. unit tests, libraries)
    keep the old behavior — readers default to Allow."""
    eng = PermissionEngine(mode=Mode.SAFE, workspace=None)
    assert isinstance(eng.check("read_file", "/etc/hosts"), Allow)


def test_workspace_bash_unaffected_by_boundary(tmp_path: Path):
    """The boundary is for PATH_TOOLS only; bash continues to be governed
    by its rule-based allow/deny logic, not by cwd."""
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    # Built-in allow still wins.
    assert isinstance(eng.check("bash", "ls /etc"), Allow)
    # Unknown bash command still Asks (mode default), no special workspace logic.
    d = eng.check("bash", "make something")
    assert isinstance(d, Ask)


def test_workspace_builtin_deny_still_wins(tmp_path: Path):
    eng = PermissionEngine(mode=Mode.SAFE, workspace=tmp_path)
    d = eng.check("bash", "rm -rf /")
    assert isinstance(d, Deny)


# ---------- end-to-end with the bash tool ----------

async def test_bash_uses_engine_for_deny():
    from codey.tools.bash import BashTool
    eng = PermissionEngine(mode=Mode.SAFE)
    tool = BashTool(engine=eng, approve=None)
    out = await tool.run({"command": "rm -rf /tmp/whatever"})
    assert out.startswith("error: blocked by permission rule")


async def test_bash_yolo_runs_unknown_command(tmp_path: Path):
    from codey.tools.bash import BashTool
    eng = PermissionEngine(mode=Mode.YOLO)
    tool = BashTool(engine=eng, approve=None)
    out = await tool.run({"command": f"touch {tmp_path / 'marker'}"})
    assert "exit=0" in out
    assert (tmp_path / "marker").exists()


async def test_bash_safe_unknown_calls_approver():
    from codey.tools import Verdict
    from codey.tools.bash import BashTool
    eng = PermissionEngine(mode=Mode.SAFE)
    calls = []
    def approve(ctx):
        calls.append(ctx)
        return Verdict(allowed=False)
    tool = BashTool(engine=eng, approve=approve)
    out = await tool.run({"command": "npm install"})
    assert len(calls) == 1
    assert calls[0]["tool"] == "bash"
    assert calls[0]["command"] == "npm install"
    assert calls[0]["suggested_pattern"] == "npm install*"
    assert "denied" in out


async def test_bash_safe_allowlisted_skips_approver():
    from codey.tools.bash import BashTool
    eng = PermissionEngine(mode=Mode.SAFE)
    calls = []
    def approve(ctx):
        calls.append(ctx); return True
    tool = BashTool(engine=eng, approve=approve)
    out = await tool.run({"command": "echo hi"})
    assert calls == []
    assert "hi" in out


async def test_bash_remember_appends_user_rule(tmp_path: Path):
    """Verdict.remember should append a rule via the engine helpers."""
    from codey.tools import Verdict
    from codey.tools.bash import BashTool
    user_path = tmp_path / "perm.toml"
    eng = PermissionEngine.load(
        user_path=user_path,
        project_path=tmp_path / "_proj.toml",
    )
    def approve(ctx):
        return Verdict(
            allowed=True,
            remember=True,
            remember_action="allow",
            remember_pattern="npm test*",
            remember_scope="user",
        )
    # Monkeypatch USER_PERMISSIONS_PATH used by append_user_rule when no
    # path is passed in by the host.
    import codey.permissions as perms
    orig = perms.USER_PERMISSIONS_PATH
    perms.USER_PERMISSIONS_PATH = user_path
    try:
        tool = BashTool(engine=eng, approve=approve)
        out = await tool.run({"command": "npm test"})
    finally:
        perms.USER_PERMISSIONS_PATH = orig
    assert "exit=" in out  # the command actually ran
    assert any(r.pattern == "npm test*" and r.action == "allow"
               for r in eng.user_rules)
    # And persisted to disk:
    assert "npm test*" in user_path.read_text()


async def test_write_file_deny_via_project_rule(tmp_path: Path):
    from codey.tools.write_file import WriteFileTool
    eng = PermissionEngine(
        mode=Mode.SAFE,
        project_rules=[Rule("write_file", "/etc/*", "deny", "protected")],
    )
    tool = WriteFileTool(engine=eng, approve=None)
    out = await tool.run({"path": "/etc/passwd", "content": "x"})
    assert out.startswith("error: blocked by permission rule")
    assert "protected" in out
