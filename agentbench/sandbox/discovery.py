"""Finding the executor binary.

Resolution order, first hit wins:

1. `AGENTBENCH_EXECUTOR` — an explicit path, for development and for pinning
   a build in CI.
2. Alongside the installed package, where the wheel puts it.
3. On PATH, for a separately installed binary.
4. `rust/target/{release,debug}` relative to the repo, so a `cargo build` in
   a checkout is picked up without any further setup.

Returning None is normal, not an error: an sdist install has no binary and
falls back to the Python Docker executor.
"""

import os
import shutil
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

    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return Path(on_path)

    repo_root = Path(__file__).parent.parent.parent
    for profile in ("release", "debug"):
        built = repo_root / "rust" / "target" / profile / BINARY_NAME
        if built.is_file():
            return built

    return None
