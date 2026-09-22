"""Task and TaskSet schemas.

Task files are executed with the privileges of whoever runs the benchmark:
the repo is cloned or copied host-side before any sandbox exists. Fields that
reach a command line are validated here, at load, so a malformed or hostile
task set is rejected before anything runs. See docs/SECURITY.md.
"""

import re
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

# Transports git can be asked for. `ext::` is excluded deliberately: it runs
# an arbitrary command, and while modern git refuses it by default, a user
# with protocol.ext.allow=always in their config would not be protected.
_ALLOWED_URL_SCHEMES = {"https", "http", "ssh", "git", "file"}
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)::?")
# scp-style remotes: git@github.com:org/repo.git
_SCP_LIKE = re.compile(r"^[^/\s]+@[^/\s]+:")
# Conservative: real refs are shas, branch and tag names, refs/... paths.
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*$")


class Task(BaseModel):
    id: str
    repo_url: str  # git URL or local path
    setup_commands: list[str]  # e.g. install deps
    failing_test_command: str  # e.g. "pytest tests/test_x.py"
    full_test_command: str = "pytest"  # for regression detection
    difficulty: str | None = None
    # Git ref (commit/tag/branch) to check out after cloning. Pin this for
    # real repos: an unpinned upstream makes results irreproducible the moment
    # the default branch moves.
    repo_ref: str | None = None
    # Which repository this task exercises. Several tasks usually share one.
    # Scoring a repo (rather than a model) means grouping runs by this, so it
    # is recorded on every run from the start — back-filling it later, once
    # real runs exist, is not possible.
    repo_id: str | None = None

    @field_validator("repo_url")
    @classmethod
    def _check_repo_url(cls, value: str) -> str:
        if value.startswith("-"):
            # git parses a leading dash as an option, not a repository
            raise ValueError(f"repo_url may not start with '-': {value!r}")
        match = _SCHEME.match(value)
        if match and not _SCP_LIKE.match(value):
            scheme = match.group(1).lower()
            if scheme not in _ALLOWED_URL_SCHEMES:
                raise ValueError(
                    f"repo_url uses the {scheme!r} transport, which is not allowed. "
                    f"Permitted: {', '.join(sorted(_ALLOWED_URL_SCHEMES))}, an "
                    f"scp-style remote, or a local path."
                )
        return value

    @field_validator("repo_ref")
    @classmethod
    def _check_repo_ref(cls, value: str | None) -> str | None:
        # `git checkout -- <ref>` is not accepted, so the ref itself has to be
        # safe: a leading dash reaches git as an option, and --orphan on an
        # unvalidated ref rewrites the checkout.
        if value is not None and not _SAFE_REF.match(value):
            raise ValueError(
                f"repo_ref is not a plausible git ref: {value!r}. "
                f"Expected a commit sha, branch, tag or refs/... path."
            )
        return value

    @model_validator(mode="after")
    def _default_repo_id(self) -> "Task":
        if not self.repo_id:
            self.repo_id = Path(self.repo_url.rstrip("/")).name.removesuffix(".git")
        return self


class TaskSet(BaseModel):
    tasks: list[Task]
