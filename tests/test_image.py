"""The sandbox image: the published default, pulling it, and `agentbench image build`."""

import subprocess
from types import SimpleNamespace

import docker.errors
import pytest

from agentbench import cli
from agentbench.sandbox.docker_executor import ImageUnavailable, ensure_image
from agentbench.sandbox.image import (
    DEFAULT_IMAGE,
    IMAGE_DIGEST,
    IMAGE_REPOSITORY,
    LOCAL_IMAGE,
    build_image,
    dockerfile,
)


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


# --- the published image, pulled before a benchmark starts -----------------


def test_the_default_image_is_pinned_by_digest(monkeypatch):
    assert DEFAULT_IMAGE.startswith(f"{IMAGE_REPOSITORY}@sha256:")
    assert len(IMAGE_DIGEST) == len("sha256:") + 64
    monkeypatch.setattr("sys.argv", ["agentbench", "--tasks", "t.yaml", "--model", "m"])
    assert cli.parse_args().image == DEFAULT_IMAGE


class _FakeImages:
    def __init__(self, present, pull_error=None):
        self.present = present
        self.pull_error = pull_error
        self.pulled = []

    def get(self, image):
        if image not in self.present:
            raise docker.errors.ImageNotFound("no such image")

    def pull(self, image):
        if self.pull_error:
            raise self.pull_error
        self.pulled.append(image)


def _fake_client(monkeypatch, images, ping_error=None):
    def ping():
        if ping_error:
            raise ping_error

    client = SimpleNamespace(ping=ping, images=images)
    monkeypatch.setattr("agentbench.sandbox.docker_executor.from_env", lambda: client)


def test_a_present_image_is_not_pulled(monkeypatch):
    images = _FakeImages(present={"img"})
    _fake_client(monkeypatch, images)
    ensure_image("img")
    assert images.pulled == []


def test_a_missing_image_is_pulled_once(monkeypatch):
    images = _FakeImages(present=set())
    _fake_client(monkeypatch, images)
    ensure_image("img")
    assert images.pulled == ["img"]


def test_a_failed_pull_says_why_and_how_to_build_instead(monkeypatch):
    denied = docker.errors.APIError("500", explanation="denied: requested access is forbidden")
    _fake_client(monkeypatch, _FakeImages(present=set(), pull_error=denied))
    with pytest.raises(ImageUnavailable) as err:
        ensure_image("ghcr.io/x/y@sha256:abc")
    message = str(err.value)
    assert "ghcr.io/x/y@sha256:abc" in message and "requested access is forbidden" in message
    assert "agentbench image build" in message and f"--image {LOCAL_IMAGE}" in message


def test_no_daemon_is_reported_as_such(monkeypatch):
    _fake_client(monkeypatch, _FakeImages(set()), ping_error=docker.errors.DockerException("x"))
    with pytest.raises(ImageUnavailable, match="Is Docker running"):
        ensure_image("img")


def test_the_cli_exits_with_the_pull_error_before_running_anything(monkeypatch, tmp_path):
    tasks = tmp_path / "t.yaml"
    tasks.write_text("tasks: []\n")
    _fake_client(monkeypatch, _FakeImages(set(), pull_error=docker.errors.NotFound("gone")))
    monkeypatch.setattr("sys.argv", ["agentbench", "--tasks", str(tasks), "--model", "m"])
    monkeypatch.setattr(cli, "make_executor", pytest.fail)
    with pytest.raises(SystemExit, match="cannot pull the sandbox image"):
        cli.main()
