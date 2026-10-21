# Contributing

## Getting set up

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -m "not docker"
```

That is the whole setup for most work. The offline suite needs **no Docker,
no Rust and no API keys**, and it is what gates every pull request.

Two optional extras, only if you are touching what they cover:

```sh
.venv/bin/agentgauge image build           # the sandbox image
.venv/bin/pytest tests/ -m docker          # the sandbox, for real

cd rust && cargo build --release           # the Rust executor
cargo test --all && cargo clippy --all-targets -- -D warnings
```

You do not need Rust to work on this. The Python package must keep working
with no binary present, and there is a test that says so.

## What CI requires

Everything below runs on every pull request, and all of it can be run locally:

```sh
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest tests/ -m "not docker"    # with a coverage floor
```

`mypy` runs `strict` over `agentgauge`, and a notch below over tests and
scripts — a fake's signature is documented by the protocol it stands in for.

Fixture repositories under `agentgauge/tasks/fixtures/` are excluded from
both ruff and mypy. They contain deliberate bugs, and a `--fix` run that
quietly repaired one would turn a benchmark task into a free pass.

## The shape of the codebase

Two seams carry most of the design:

- **`SandboxExecutor`** (`agentgauge/sandbox/executor.py`) is the only way
  orchestration touches a container runtime. `docs/PROTOCOL.md` is its wire
  form; `tests/conformance/` is the normative definition of correct
  behaviour. Adding an executor means adding one entry to `IMPLEMENTATIONS`
  in `tests/conformance/conftest.py` and making the suite pass.
- **`ModelProvider`** (`agentgauge/providers/base.py`) is the only way the
  agent loop touches a model API.

Changing behaviour at either seam means changing the contract, which means
changing the tests that define it. That is the intended friction.

## Things worth knowing before you change them

- **A benchmark task is a claim** — this named test fails, the rest of the
  suite passes. `tests/test_task_integrity.py` enforces it. If you add or
  edit a fixture, that suite tells you whether the task still means what it
  says.
- **Unknown is not the same as empty.** `cost_usd` is `None` for a model with
  no price on file rather than a guessed number; `regressions` is `None` when
  detection was unavailable rather than `[]`; `tampered_tests` is `None` when
  the check never ran. Several of these were bugs once. Please keep the
  distinction.
- **Harness errors are not model failures.** A Docker fault or a rate limit
  is an error rate, not a lower pass rate.
- **Task files are trusted input** and partly execute host-side. See
  `docs/SECURITY.md` before touching `_materialize_repo` or the `Task`
  validators.

## Adding a model

Add an entry to `agentgauge/providers/pricing.yaml` and route it in
`make_provider`. A model with no price reports **no cost at all** rather than
a figure derived from another model's rate — please do not add a fallback
that guesses.

## Pull requests

- Small, single-purpose commits that each leave the tree green. A commit that
  mixes a refactor with a fix cannot be reviewed or bisected.
- Say what you could not verify. "I have no Docker so the sandbox half only
  ran in CI" is useful; silence about it is not.
- Tests that describe the behaviour, not the implementation. A docstring
  explaining *why* a property matters is worth more than the assertion.

## Licence

MIT. By contributing you agree your work ships under it.
