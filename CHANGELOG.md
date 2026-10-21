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

- **Replay tests for provider responses.** Real Gemini responses recorded
  during live runs, and OpenAI/Anthropic responses built from their SDK
  schemas, run through parsing, the loop and pricing with no API key.

- **Gemini models.** `--model gemini-*` with `GEMINI_API_KEY`, through
  Google's OpenAI-compatible endpoint. Thinking tokens, which that endpoint
  leaves out of `completion_tokens`, are counted as output, and Gemini 3's
  per-call `thought_signature` is sent back on the following turn.

- **Rust sandbox executor.** A three-crate workspace (`rust/`) implementing
  the executor protocol over Docker, plus a Python client that speaks it to
  any conforming binary. `--executor {auto,rust,python}` selects one; `auto`
  falls back to the Python executor when no binary is present.
- **An executor contract.** `docs/PROTOCOL.md` specifies the wire form and
  `tests/conformance/` is its normative definition — every implementation is
  held to the same suite.
- **Packaging.** Published to PyPI as `agentgauge`, the same name as the
  command and the import. maturin builds the Rust
  binary into its own wheel (`agentgauge-executord`), which `agentgauge`
  depends on for macOS and Linux, so `pip install` brings the binary with no
  Rust toolchain. Wheels cover macOS arm64/x86_64 and Linux x86_64/aarch64 on
  glibc and musl. Each tag publishes to PyPI by trusted publishing and to a
  GitHub Release with standalone binaries and `SHA256SUMS`, after installing
  every wheel in a clean environment on its platform.
- **A published sandbox image.** `--image` defaults to
  `ghcr.io/anmol0b/agentgauge-base`, pinned by digest and pulled on first
  use, so an install runs tasks with no checkout. A release refuses to
  publish unless that digest is public and built from the Dockerfile it
  ships. Where the registry cannot be reached, `agentgauge image build`
  builds the same image from the Dockerfile, which now ships in the package
  (`agentgauge/sandbox/base.Dockerfile`, formerly `docker/base.Dockerfile`).
  An image that cannot be pulled, or a Docker daemon that is not running, is
  one error before any run starts.
- **A sandbox benchmark** (`bench/sandbox_bench.py`) and its first results
  (`docs/BENCHMARKS.md`), measured rather than asserted.
- Medium and hard benchmark tasks, including one where the obvious fix passes
  the failing test and breaks two passing ones.
- **Repository scoring.** Runs group by `repo_id`, so the report answers how
  well agents work *in a codebase* and not only which model is better. A
  profile rather than a single score, and repositories evaluated against
  different models are explicitly not compared.
- **A record of what the agent did.** Every run stores an abbreviated
  transcript: the turns taken, each tool call with its arguments, an excerpt
  of each result, and whether the model had to be nudged. Previously a failed
  run was explainable only by its diff and stop reason. Clipped to stay
  storable for every run, and every clip states the size it came from.
- Per-run phase timings (`setup_s`, `agent_s`, `grading_s`).
- A strict CI suite: lint, format, `mypy --strict`, a Python version matrix,
  a coverage floor, the Docker sandbox, Rust lint and tests, dependency audit.
- `docs/SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates.

### Changed

- **Executor protocol 2.0.** Length-prefixed framing replaces one-JSON-object-
  per-line, removing the maximum message size a delimiter-based format forces
  on every reader. The client now honours the capabilities an executor
  declares instead of requesting streaming regardless. `network_allowlist` and
  `exec_timeout_s` are gone from `SandboxPolicy` — nothing honoured either.

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

- **OpenAI cached input is billed at its cached rate.** It was counted as
  ordinary input, overstating cost by up to the cache discount on every turn
  after the first few.
- The Rust executor installed from its wheel is found when the venv is not
  activated, and under pipx and `uv tool`; previously these silently used the
  Python executor. A checkout's own `cargo build` still takes precedence.
- The sandbox base image is pinned by digest and its pytest by version.
  `python:3.12-slim` is a moving tag, so the environment a task ran in drifted
  between builds — which makes an old run and a new one incomparable.

- Streamed output was implemented through the protocol and both executors but
  never wired to the tool that runs tests, so a long test run still showed
  nothing until it finished. The harness now prints a throttled progress line
  per run.
- `--repo` left `repo_id` at its original value, filing every run under a
  repository the task was no longer using.

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
