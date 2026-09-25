# Pinned by digest, not by tag. `python:3.12-slim` moves, so an image built
# this week and one built next week are different sandboxes — and a benchmark
# whose environment drifts cannot compare a run against an older one. pytest
# is pinned for the same reason: it is what executes the task's tests.
#
# To update, deliberately:
#   docker pull python:3.12-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

# Test tooling and git baked in: sandbox containers run with network disabled,
# so tasks cannot pip install at run time. git is needed for diff capture, the
# repo file listing in the task instruction, and restoring a fix's test file.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==9.1.1
