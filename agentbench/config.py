"""Env var loading. Secrets live in the process environment only — nothing is
persisted to disk or the database (hard boundary §2.4).
"""

import os


def get_api_key(provider: str) -> str:
    """Return the API key for `provider` ("anthropic" | "openai") from env."""
    name = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider]
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; export it before running")
    return value
