# Changelog

Notable changes, newest first. Format follows [Keep a Changelog][kac]; this
project will follow [semantic versioning][semver] from 0.1.0 onward.

The sandbox executor protocol is versioned separately — see
`docs/PROTOCOL.md`.

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/

## Unreleased

Nothing has been released yet. 0.1.0 will be the first tag.

### Added

- **Rust sandbox executor.** A three-crate workspace (`rust/`) implementing
  the executor protocol over Docker, plus a Python client that speaks it to
  any conforming binary. `--executor {auto,rust,python}` selects one; `auto`
  falls back to the Python executor when no binary is present.
- **An executor contract.** `docs/PROTOCOL.md` specifies the wire form and
  `tests/conformance/` is its normative definition — every implementation is
  held to the same suite.
- **Packaging.** maturin builds the binary into a wheel
  (`agentbench-executord`), kept as a separate distribution so the Python
  package installs with no Rust anywhere. A release workflow builds wheels
  for macOS arm64/x86_64 and manylinux x86_64/aarch64, plus the sdist.
- **A sandbox benchmark** (`bench/sandbox_bench.py`) and its first results
  (`docs/BENCHMARKS.md`), measured rather than asserted.
- Medium and hard benchmark tasks, including one where the obvious fix passes
  the failing test and breaks two passing ones.
- **Repository scoring.** Runs group by `repo_id`, so the report answers how
  well agents work *in a codebase* and not only which model is better. A
  profile rather than a single score, and repositories evaluated against
  different models are explicitly not compared.
- Per-run phase timings (`setup_s`, `agent_s`, `grading_s`).
- A strict CI suite: lint, format, `mypy --strict`, a Python version matrix,
  a coverage floor, the Docker sandbox, Rust lint and tests, dependency audit.
- `docs/SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates.

### Changed — how results are reported

These change what the numbers mean, not just how they look:

- **Pass rates carry a 95% confidence interval.** At small N a bare
  percentage reads as more precise than it is.
- **Harness errors are no longer model failures.** A Docker fault or a rate
  limit is an error rate, excluded from the pass-rate denominator. All
  attempts erroring reports `n/a`, never 0%.
- **Editing a test disqualifies a run,** regardless of exit code.
- **Reports cover one invocation.** Previously every historical run was
  blended into one pass rate; `--all-history` opts back in.
- **Unpriced models report no cost** instead of a figure derived from another
  model's rate. Anthropic cache tokens are now billed.
- **Undetected is distinguished from empty** for regressions and the
  test-tampering check.
- Model leaderboard, difficulty breakdown, and tool-call and token spread in
  the report.

### Fixed

- A 64 KiB line limit in the subprocess client hung every call after a
  response larger than that — a file read or any substantial command output.
- Docker exec timeouts returned without stopping the command, which then held
  the container's CPU for the rest of the run.
- Regression detection read a missing junit report as "nothing regressed".
- A stale junit report could be read back as the post-fix result.
- Binary files in a repository were unreadable through the executor.
- The HTML report did not escape model-authored content.
- The docker client was shared across threads.
- `repo_url` and `repo_ref` from a task file reached a git command line
  unvalidated; `--orphan` as a ref was accepted as an option.
