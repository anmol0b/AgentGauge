"""The sandbox image: the shipped Dockerfile and `agentbench image build`."""

import subprocess

import pytest

from agentbench import cli
from agentbench.sandbox.image import LOCAL_IMAGE, build_image, dockerfile


def test_the_dockerfile_ships_in_the_package():
    # Read through importlib.resources, the way an installed wheel serves it.
    assert dockerfile().startswith(b"#")
    assert b"FROM python:3.12-slim@sha256:" in dockerfile()


def _fake_docker(monkeypatch, returncode=0):
    calls = []

    def run(argv, input):
        calls.append((argv, input))
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.run", run)
    return calls


def test_build_feeds_the_dockerfile_on_stdin_with_no_context(monkeypatch):
    calls = _fake_docker(monkeypatch)
    build_image("mine:1")
    assert calls == [(["/usr/bin/docker", "build", "--tag", "mine:1", "-"], dockerfile())]


def test_build_reports_a_failed_build(monkeypatch):
    _fake_docker(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError, match="exit 1"):
        build_image()


def test_build_without_docker_says_so(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="docker command is not on PATH"):
        build_image()


def test_image_build_subcommand_tags_the_local_image(monkeypatch, capsys):
    calls = _fake_docker(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentbench", "image", "build"])
    cli.main()
    assert calls[0][0][3] == LOCAL_IMAGE
    assert f"--image {LOCAL_IMAGE}" in capsys.readouterr().out


def test_image_build_failure_exits_with_the_reason(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("sys.argv", ["agentbench", "image", "build"])
    with pytest.raises(SystemExit, match="docker command is not on PATH"):
        cli.main()
