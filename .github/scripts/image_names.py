"""Print the sandbox image's names as GITHUB_OUTPUT lines.

They are read from agentbench/sandbox/image.py rather than repeated in each
workflow, so a rename touches that one file. `dockerfile_tag` names the build
of the Dockerfile as it is in this checkout: the image workflow pushes it, and
the release checks the pinned digest is what that tag points at.

usage: python3 .github/scripts/image_names.py >> "$GITHUB_OUTPUT"
"""

import hashlib
import runpy
from pathlib import Path

image = runpy.run_path("agentbench/sandbox/image.py")
dockerfile = Path("agentbench/sandbox/base.Dockerfile").read_bytes()

print(f"repository={image['IMAGE_REPOSITORY']}")
print(f"digest={image['IMAGE_DIGEST']}")
# The tag part of the local name, e.g. py3.12, which the published image
# carries too.
print(f"tag={image['LOCAL_IMAGE'].rpartition(':')[2]}")
print(f"dockerfile_tag=dockerfile-{hashlib.sha256(dockerfile).hexdigest()}")
