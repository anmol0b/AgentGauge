"""Model pricing, loaded from pricing.yaml.

An unlisted model has no price. `lookup` returns None for it and callers
report cost as unknown — never as a number derived from some other model's
rate, which would be a confidently wrong figure in a benchmark whose whole
job is to report cost.
"""

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from agentbench.models.run import TokenUsage

_PRICING_FILE = Path(__file__).parent / "pricing.yaml"

# Standard Anthropic prompt-caching multipliers, used when a model's entry
# does not state its own cache rates.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


class Price(BaseModel):
    """USD per 1M tokens."""

    input: float
    output: float
    cache_write: float | None = None
    cache_read: float | None = None

    def cost(self, usage: TokenUsage) -> float:
        write_rate = (
            self.cache_write
            if self.cache_write is not None
            else self.input * _CACHE_WRITE_MULTIPLIER
        )
        read_rate = (
            self.cache_read if self.cache_read is not None else self.input * _CACHE_READ_MULTIPLIER
        )
        total = (
            usage.tokens_in * self.input
            + usage.tokens_out * self.output
            + usage.cache_write_tokens * write_rate
            + usage.cache_read_tokens * read_rate
        )
        return total / 1_000_000


@cache
def _table() -> dict[str, Price]:
    raw = yaml.safe_load(_PRICING_FILE.read_text())
    return {model: Price(**fields) for group in raw.values() for model, fields in group.items()}


def lookup(model: str) -> Price | None:
    """Longest-prefix match, so dated snapshots resolve to their base entry."""
    matches = [prefix for prefix in _table() if model.startswith(prefix)]
    if not matches:
        return None
    return _table()[max(matches, key=len)]


def known_models() -> list[str]:
    return sorted(_table())
