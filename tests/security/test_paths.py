"""The filesystem sandbox: traversal, symlink escape and clobber protection.

Two policies exist on purpose. `PathPolicy(roots=[...])` is the MCP/REST sandbox where the
caller is an untrusted agent; `PathPolicy(roots=None)` is the CLI, where the human running
the command is the principal and already has their own shell.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from musiccontext.engine.context import load_settings
from musiccontext.errors import InvalidPathError, PermissionDeniedError
from musiccontext.security.paths import PathPolicy, policy_from_settings


@pytest.fixture
def sandbox(tmp_path):
    """A root the policy allows, plus a victim file outside it."""
    inside = tmp_path / "sandbox"
    inside.mkdir()
    (inside / "clip.mp4").write_bytes(b"\x00\x00video")
    (tmp_path / "outside.txt").write_text("credentials")
    return inside


@pytest.fixture
def policy(sandbox):
    return PathPolicy(roots=[sandbox.resolve()])


def test_relative_traversal_out_of_the_root_is_refused(policy, sandbox, monkeypatch):
    monkeypatch.chdir(sandbox)
    with pytest.raises(PermissionDeniedError):
        policy.input_file("../outside.txt")
    with pytest.raises((PermissionDeniedError, InvalidPathError)):
        policy.input_file("../../../../../../etc/passwd")


def test_absolute_path_outside_the_root_is_refused(policy, sandbox):
    with pytest.raises(PermissionDeniedError) as ei:
        policy.input_file(sandbox.parent / "outside.txt")
    assert "MUSICCONTEXT_ROOTS" in (ei.value.hint or "")


@pytest.mark.parametrize("method", ["input_file", "input_dir", "output_file"])
def test_nul_byte_in_path_is_refused(policy, method):
    with pytest.raises(InvalidPathError) as ei:
        getattr(policy, method)("clip\x00.mp4")
    assert "NUL" in ei.value.message


def test_symlink_inside_the_root_pointing_out_is_refused(policy, sandbox):
    """Containment must be checked AFTER resolving, or a symlink is a free escape hatch."""
    link = sandbox / "innocent.mp4"
    link.symlink_to(sandbox.parent / "outside.txt")
    with pytest.raises(PermissionDeniedError):
        policy.input_file(link)


def test_input_file_rejects_a_directory(policy, sandbox):
    with pytest.raises(InvalidPathError):
        policy.input_file(sandbox)


def test_output_file_refuses_to_clobber_without_force(policy, sandbox):
    existing = sandbox / "out.musicctx"
    existing.write_text("{}")
    with pytest.raises(InvalidPathError) as ei:
        policy.output_file(existing)
    assert "--force" in (ei.value.hint or "")
    assert policy.output_file(existing, force=True) == existing.resolve()


def test_output_file_refuses_to_write_through_a_symlink(policy, sandbox):
    link = sandbox / "out.json"
    link.symlink_to(sandbox.parent / "outside.txt")
    with pytest.raises(InvalidPathError) as ei:
        policy.output_file(link, force=True)
    assert "symlink" in ei.value.message.lower()


def test_output_file_never_overwrites_a_protected_input(policy, sandbox):
    """force must not be able to destroy the very file being analysed."""
    source = sandbox / "clip.mp4"
    with pytest.raises(InvalidPathError) as ei:
        policy.output_file(source, force=True, protect=(source,))
    assert "input" in ei.value.message.lower()


def test_output_file_missing_parent_gives_an_actionable_hint(policy, sandbox):
    with pytest.raises(InvalidPathError) as ei:
        policy.output_file(sandbox / "nope" / "out.json")
    assert ei.value.hint and "create" in ei.value.hint.lower()


def test_trusted_cli_policy_allows_paths_outside_cwd(sandbox, monkeypatch):
    """roots=None is deliberate: on the CLI the user IS the principal.

    They could `cat` the same file themselves, so refusing it would only be theatre.
    The sandbox exists for MCP/REST, where the caller is an agent, not the user.
    """
    monkeypatch.chdir(sandbox)
    outside = sandbox.parent / "outside.txt"
    assert PathPolicy(roots=None).input_file(outside) == outside.resolve()


def test_policy_from_settings_restricted_includes_cwd_and_env_roots(tmp_home, tmp_path, monkeypatch):
    extra = tmp_path / "media"
    extra.mkdir()
    monkeypatch.setenv("MUSICCONTEXT_ROOTS", str(extra))
    p = policy_from_settings(load_settings(), restricted=True)
    assert p.roots is not None
    assert Path.cwd().resolve() in p.roots
    assert extra.resolve() in p.roots


def test_policy_from_settings_unrestricted_is_the_cli_policy(tmp_home):
    assert policy_from_settings(load_settings(), restricted=False).roots is None


def test_multiple_env_roots_are_split_on_the_platform_separator(tmp_home, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("MUSICCONTEXT_ROOTS", os.pathsep.join([str(a), str(b)]))
    roots = policy_from_settings(load_settings(), restricted=True).roots
    assert {a.resolve(), b.resolve()} <= set(roots or [])
