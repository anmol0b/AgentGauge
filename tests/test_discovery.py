"""Executor binary discovery.

Returning None is a supported outcome, not a failure: an sdist install has no
binary and falls back to the Python Docker executor.
"""

import stat
from pathlib import Path

import pytest

from agentbench.sandbox.discovery import BINARY_NAME, find_executor_binary


def _fake_binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_explicit_env_var_wins(tmp_path, monkeypatch):
    binary = _fake_binary(tmp_path / BINARY_NAME)
    monkeypatch.setenv("AGENTBENCH_EXECUTOR", str(binary))
    assert find_executor_binary() == binary


def test_a_wrong_explicit_path_is_an_error_not_a_fallback(tmp_path, monkeypatch):
    """Silently ignoring it would run the wrong executor and say nothing."""
    monkeypatch.setenv("AGENTBENCH_EXECUTOR", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="AGENTBENCH_EXECUTOR"):
        find_executor_binary()


def test_finds_the_wheel_install_without_an_activated_venv(tmp_path, monkeypatch):
    """`.venv/bin/agentbench` run directly has the venv's bin off PATH."""
    monkeypatch.delenv("AGENTBENCH_EXECUTOR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    binary = _fake_binary(tmp_path / "venv" / "bin" / BINARY_NAME)
    monkeypatch.setattr(
        "agentbench.sandbox.discovery.sys.executable", str(binary.parent / "python")
    )
    monkeypatch.setattr(
        "agentbench.sandbox.discovery.Path.is_file",
        lambda self: str(self) == str(binary),
    )
    assert find_executor_binary() == binary


def test_a_checkout_build_beats_the_installed_wheel(tmp_path, monkeypatch):
    """`pip install -e .` installs the published executor; a checkout runs its own."""
    import agentbench.sandbox.discovery as discovery

    monkeypatch.delenv("AGENTBENCH_EXECUTOR", raising=False)
    repo_root = Path(discovery.__file__).parent.parent.parent
    built = repo_root / "rust" / "target" / "release" / BINARY_NAME
    installed = tmp_path / "venv" / "bin" / BINARY_NAME
    monkeypatch.setattr(
        "agentbench.sandbox.discovery.sys.executable", str(installed.parent / "python")
    )
    monkeypatch.setattr(
        "agentbench.sandbox.discovery.Path.is_file",
        lambda self: str(self) in {str(built), str(installed)},
    )
    assert find_executor_binary() == built


def test_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTBENCH_EXECUTOR", raising=False)
    binary = _fake_binary(tmp_path / "bin" / BINARY_NAME)
    monkeypatch.setenv("PATH", str(binary.parent))
    monkeypatch.setattr(
        "agentbench.sandbox.discovery.Path.is_file",
        lambda self: str(self) == str(binary),
    )
    assert find_executor_binary() == binary


def test_none_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTBENCH_EXECUTOR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr("agentbench.sandbox.discovery.Path.is_file", lambda self: False)
    assert find_executor_binary() is None
