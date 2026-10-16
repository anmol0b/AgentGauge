"""Finding the executor binary.

Resolution order, first hit wins:

1. `AGENTBENCH_EXECUTOR` — an explicit path, for development and for pinning
   a build in CI.
2. Alongside the installed package.
3. The running interpreter's scripts directory, where the
   `agentbench-executord` wheel installs it. PATH alone misses it when a venv
   is not activated (`.venv/bin/agentbench ...`) or under pipx and `uv tool`,
   which expose only the entry point.
4. On PATH, for a separately installed binary.
5. `rust/target/{release,debug}` relative to the repo, so a `cargo build` in
   a checkout is picked up without any further setup.

Returning None is normal, not an error: an sdist install has no binary and
falls back to the Python Docker executor.
"""

import os
import shutil
import sys
import sysconfig
from pathlib import Path

BINARY_NAME = "agentbench-executord"


def find_executor_binary() -> Path | None:
    explicit = os.environ.get("AGENTBENCH_EXECUTOR")
    if explicit:
        candidate = Path(explicit).expanduser()
        # An explicit path that is wrong is a mistake worth surfacing, not
        # something to silently fall back from.
        if not candidate.is_file():
            raise FileNotFoundError(f"AGENTBENCH_EXECUTOR={explicit} is not a file")
        return candidate

    packaged = Path(__file__).parent.parent / "bin" / BINARY_NAME
    if packaged.is_file():
        return packaged

    for scripts in _scripts_dirs():
        installed = scripts / BINARY_NAME
        if installed.is_file():
            return installed

    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return Path(on_path)

    repo_root = Path(__file__).parent.parent.parent
    for profile in ("release", "debug"):
        built = repo_root / "rust" / "target" / profile / BINARY_NAME
        if built.is_file():
            return built

    return None


def _scripts_dirs() -> list[Path]:
    # sysconfig is the install scheme's answer; the interpreter's own directory
    # covers a venv whose python is a symlink sysconfig resolves elsewhere.
    dirs = [Path(sysconfig.get_path("scripts")), Path(sys.executable).parent]
    return list(dict.fromkeys(dirs))
