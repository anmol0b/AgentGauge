"""Resource limits and network policy for sandboxes.

v1 note: containers run with network_mode="none". The agent loop calls model
provider APIs from the *host* process, so the sandbox never needs egress —
this satisfies the network-allowlist intent by construction. If a future
executor runs the agent inside the sandbox, `network_allowlist` is where the
provider API hosts get declared.
"""

from pydantic import BaseModel, Field


class SandboxPolicy(BaseModel):
    mem_limit: str = "2g"
    nano_cpus: int = 1_000_000_000  # 1 CPU
    pids_limit: int = 256
    network_mode: str = "none"
    network_allowlist: list[str] = Field(default_factory=list)  # v2+, see note above
    exec_timeout_s: int = 120
