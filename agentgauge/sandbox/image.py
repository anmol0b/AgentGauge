"""The sandbox image: the published build every run defaults to, and building one locally.

This module imports nothing from the package, so the release workflow can
read the names below with `runpy` and they live in this one place.
"""

import shutil
import subprocess
from importlib.resources import files

# Where the sandbox image is published, and the build of it this version runs
# in. Pinned by digest for the reason the Dockerfile pins its base: a tag can
# move, and a sandbox that drifts cannot compare a run against an older one.
#
# To update, deliberately: run the "Sandbox image" workflow, which builds the
# Dockerfile below and pushes it here, and paste the digest it reports. A
# release refuses to publish unless this digest is public and was built from
# the Dockerfile being released.
IMAGE_REPOSITORY = "ghcr.io/anmol0b/agentgauge-base"
IMAGE_DIGEST = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
DEFAULT_IMAGE = f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}"

# What `agentgauge image build` tags, and what the docker tests and CI use: an
# image built from the Dockerfile in this checkout or install.
LOCAL_IMAGE = "agentgauge-base:py3.12"


def dockerfile() -> bytes:
    """The sandbox Dockerfile, as shipped in the package."""
    return files("agentgauge.sandbox").joinpath("base.Dockerfile").read_bytes()


def build_image(tag: str = LOCAL_IMAGE) -> None:
    """Build the sandbox image with the docker CLI, streaming its output.

    The Dockerfile has no COPY, so it is fed on stdin with no build context:
    nothing on disk is needed beyond the package itself.
    """
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("the docker command is not on PATH; install Docker to build the image")
    result = subprocess.run([docker, "build", "--tag", tag, "-"], input=dockerfile())
    if result.returncode != 0:
        raise RuntimeError(f"docker build failed (exit {result.returncode})")
