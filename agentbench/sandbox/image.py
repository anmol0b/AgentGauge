"""The sandbox image, built locally from the Dockerfile shipped in the package."""

import shutil
import subprocess
from importlib.resources import files

# What `agentbench image build` tags, and what the docker tests and CI use: an
# image built from the Dockerfile in this checkout or install.
LOCAL_IMAGE = "agentbench-base:py3.12"


def dockerfile() -> bytes:
    """The sandbox Dockerfile, as shipped in the package."""
    return files("agentbench.sandbox").joinpath("base.Dockerfile").read_bytes()


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
