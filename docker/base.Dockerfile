FROM python:3.12-slim
# Test tooling and git baked in: sandbox containers run with network disabled,
# so tasks cannot pip install at run time. git is needed for diff capture and
# the repo file listing in the task instruction.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest
