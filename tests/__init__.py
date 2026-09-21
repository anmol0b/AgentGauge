"""Test package.

This file is load-bearing: `tests.fakes` is imported by several modules, and
without it pytest puts `tests/` on sys.path rather than the repo root, so
`from tests.fakes import ...` resolves only when pytest is invoked as
`python -m pytest` (which adds the CWD). Bare `pytest` — what CI runs — fails.
"""
