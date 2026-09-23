"""Resource limits for sandboxes.

Containers run with network_mode="none". The agent loop calls model provider
APIs from the *host*, so the sandbox never needs egress. An executor that ran
the agent *inside* the sandbox would need an egress allowlist; that design
does not exist, so the protocol does not carry a field for it — a published
field that nothing honours invites implementers to support it.

Command timeouts are not here either: every exec takes one explicitly, and a
policy field duplicating a required argument is a second source of truth that
silently loses.
"""

from pydantic import BaseModel


class SandboxPolicy(BaseModel):
    mem_limit: str = "2g"
    nano_cpus: int = 1_000_000_000  # 1 CPU
    pids_limit: int = 256
    network_mode: str = "none"
