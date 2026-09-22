## What this changes

<!-- One or two sentences. The commits carry the detail. -->

## What you verified

<!-- Which of these you actually ran, not which ones exist. -->

- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy`
- [ ] `pytest tests/ -m "not docker"`
- [ ] `pytest tests/ -m docker` (needs Docker and the sandbox image)
- [ ] `cargo test --all && cargo clippy --all-targets -- -D warnings` (if `rust/` changed)

## What you could not verify

<!-- No Docker? No Rust toolchain? Say so — it tells a reviewer where to look
     rather than leaving them to assume it was all covered. "Nothing" is a
     fine answer. -->

## Behaviour at a contract

<!-- Delete if neither applies. -->

- [ ] This changes `SandboxExecutor`, so `tests/conformance/` changed with it
- [ ] This changes a benchmark task or fixture, so `tests/test_task_integrity.py` still passes
