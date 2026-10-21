# AgentGauge

How well do AI coding agents work in your repository — and which model works best in it?

A CLI that takes a repo + a set of "fix this failing test" tasks, runs an AI coding
agent against each task inside an isolated Docker sandbox, and reports success rate,
tokens, cost, latency, tool-call count, and regressions — with N repeated runs per
task to report variance (LLM outputs are non-deterministic; variance is reported
explicitly, never hidden).

## Architecture

Orchestration never talks to Docker directly — all sandbox interaction goes through
the `SandboxExecutor` protocol (`agentgauge/sandbox/executor.py`), so the executor can
be swapped (e.g. rewritten in Rust) without touching eval logic. Providers sit behind
the `ModelProvider` protocol (`agentgauge/providers/base.py`); Anthropic and OpenAI are
two implementations of the same interface, plus OpenAI-compatible endpoints (Ollama,
Groq, ...) via `base_url`. All cross-module data is typed Pydantic v2 models. API keys
are read from env vars only, held in memory, never persisted.

```
agentgauge/
├── cli.py                 # entry point
├── models/                # Task, RunResult, VarianceReport, provider schemas
├── providers/             # ModelProvider protocol + anthropic/openai impls + pricing
├── sandbox/               # SandboxExecutor protocol + docker impl (only file importing docker)
├── agent_loop/            # tool-calling loop + tool definitions
├── orchestrator/          # single-run runner + asyncio N-run harness
├── storage/               # SQLite run history
├── reporting/             # variance aggregation + HTML report
└── tasks/                 # YAML task definitions + fixture repos
```

## Install

macOS and Linux, Python 3.12+, and a running Docker daemon:

```sh
pip install agentgauge        # or: uv tool install agentgauge
```

The package, the command and the import are all `agentgauge`. It pulls in the Rust sandbox
executor as a prebuilt wheel (macOS arm64/x86_64, Linux x86_64/aarch64, glibc
or musl), so no Rust toolchain is needed. Each GitHub Release also carries the
bare binaries with a `SHA256SUMS`, for setting `AGENTGAUGE_EXECUTOR` by hand.

Tasks run in a sandbox image, `ghcr.io/anmol0b/agentgauge-base`, which the
first run pulls. It is pinned by digest, so every install of a version runs in
the same sandbox. Where that registry cannot be reached, build the same
Dockerfile locally — it ships in the package — and point runs at it:

```sh
agentgauge image build             # tags agentgauge-base:py3.12
agentgauge --image agentgauge-base:py3.12 --tasks ... --model ...
```

## Development setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/agentgauge image build   # agentgauge-base:py3.12, for the docker tests
```

Before the first release this install needs the executor wheel built locally
— see `CONTRIBUTING.md`.

Sandboxed containers run with `network_mode=none` (the agent loop calls model APIs
from the host), so test dependencies must be baked into the image.

The base image is pinned by digest and its pytest by version, and runs default
to the published build pinned by digest. A benchmark whose sandbox drifts cannot
compare a run against an older one — `agentgauge/sandbox/base.Dockerfile` and
`agentgauge/sandbox/image.py` say how to update each pin deliberately.

## Models

- Anthropic: `export ANTHROPIC_API_KEY=...`, use `--model claude-sonnet-5`
- OpenAI: `export OPENAI_API_KEY=...`, use `--model gpt-4o`
- Gemini (free tier available): `export GEMINI_API_KEY=...`, use
  `--model gemini-3.6-flash`. Runs through Google's OpenAI-compatible
  endpoint. A free key is rate-limited, so use `--concurrency 1`; the reported
  cost is what the run would cost on a paid key.
- Ollama (free, local, no key): pull a model, set `FROM` in `ollama/Modelfile` to
  match, `ollama create agentgauge-qwen -f ollama/Modelfile`, then
  `--model ollama/agentgauge-qwen`. The Modelfile exists to raise Ollama's default
  4096-token context, which is too small for an agent loop.

One provider is built per model and shared across that model's runs, so a
missing API key fails immediately rather than after every run has errored.

Costs come from `agentgauge/providers/pricing.yaml`. A model with no entry there
reports **no cost at all** rather than a figure guessed from another model's rate —
add an entry when you benchmark a new model.

## Run

```sh
.venv/bin/agentgauge \
  --tasks agentgauge/tasks/example.yaml \
  --model ollama/agentgauge-qwen \
  --runs-per-task 3 \
  --out report.html
```

Results are appended to `agentgauge.db` (SQLite). Each invocation gets a run-set id
and the report covers **only that invocation** — pass `--all-history` to aggregate
every run in the database instead (which mixes separate sessions into one pass rate).

## Scoring a repository, not just a model

Runs are grouped by `repo_id` as well as by model, so the report answers
"how well do agents work *in this codebase*" and not only "which model is
better". That side is a **profile rather than a single score**: pass rate,
tool calls per run, tokens, cost, and how often a run caused a regression or
was disqualified. A repo where agents pass often but take thirty tool calls
and break a neighbouring test is not the same as one where they pass often,
cheaply and cleanly.

Repositories evaluated against **different models are not compared**. The
report says so instead of ranking them — otherwise a repo tested only with a
weak model looks hostile to agents when the difference is the model.

## Scoring rules

- **Pass rates carry a 95% confidence interval.** At N=3, three passes is not
  "100%" but "somewhere above ~44%". Overlapping intervals between two models mean
  the run does not separate them.
- **Harness errors are not model failures.** A Docker fault, rate limit or failed
  setup command is reported as an error rate and excluded from the pass-rate
  denominator. If every attempt errored, pass rate is `n/a`, never 0%.
- **Editing tests disqualifies a run.** Grading is the exit code of the failing
  test command, so an agent that rewrites the test would otherwise score a pass.
  Runs that touch test files are recorded and never counted as passing.
- **Every run records why the agent stopped** (`end_turn`, `max_turns`,
  `max_tokens`), so a failure can be attributed rather than guessed at.
- **Every run records what the agent did** — turns, tool calls, result
  excerpts, and whether it had to be nudged. Abbreviated so it can be kept for
  every run; each clip states the size it came from.
- **Regressions come from a junit report**, which needs a pytest-compatible suite.
  Where it is missing or unparseable, runs read "not detected", not "none".

## Tasks

`agentgauge/tasks/example.yaml` holds seeded-bug tasks over local fixture repos —
fast, offline, and useful for smoke-testing the harness. Difficulty runs from
one-line fixes to `pagination-partial-page-bug`, where the obvious fix passes the
failing test and breaks two passing ones.

They are toy repos, so a high pass rate on them is not evidence a model is good at
real codebases. `agentgauge/tasks/real_repos.yaml` holds tasks built from real
upstream bugs — seven, from boltons, more-itertools, cachetools and toolz —
pinned at the commit before each fix, with the fix's test restored. Its header
documents how to add more.

Every real task is verified in CI against an actual clone: the declared test must
fail at the pinned commit, and applying the upstream fix must make it pass. A task
that is already solved, or that no fix resolves, fails the build rather than
quietly scoring every model at one or zero.

## Security

This tool executes code written by a language model. Agent commands run in a
container with **no network**, which is the main containment, but a container
is not a hard boundary against a determined escape — benchmark models or
repositories you distrust on a disposable machine.

**Task files are trusted input.** `repo_url` is cloned or copied on the
**host**, before any sandbox exists, so a task set is closer to a shell script
than to configuration. Fields that reach a git command line are validated, but
only run task sets you would run as a script.

Full trust boundaries, including what is deliberately not defended:
`docs/SECURITY.md`.

## Limitations

- **No paid model has been benchmarked yet.** Every run in the committed database is
  a local Ollama model at zero cost. The Anthropic and OpenAI code paths are unit
  tested but have never been exercised end to end against a live API.
- **Latency is measured under concurrency** and is not comparable across different
  `--concurrency` values. The report states this.
- **The full suite runs twice per task run** (once for a baseline, once after the
  fix) to detect regressions, which roughly doubles wall-clock on slow suites.
- **Task files are executed with host privileges** at the clone/copy step — a task
  set names paths and git URLs that the host acts on. Only run task sets you trust.
- **A model that never edits anything can burn all 25 turns** being nudged, which
  inflates its recorded cost and token count for that run.
- Regression detection and `--junitxml` assume pytest.

## Executors

The sandbox sits behind one narrow interface, so it can be reimplemented in
another language without touching evaluation logic. Two implementations ship,
and both pass the same contract:

| `--executor` | What runs |
|---|---|
| `auto` (default) | The Rust binary when one is found, else the Python one |
| `rust` | `agentgauge-executord`, a separate process speaking the protocol |
| `python` | In-process docker-py. The reference implementation and fallback |

`auto` looks for the binary at `$AGENTGAUGE_EXECUTOR`, then beside the
installed package, then in `rust/target/{release,debug}` — so a
`cargo build --release` in a checkout is picked up with no further setup, ahead
of the published one `pip install -e .` also installs — then in the Python
environment's scripts directory, then on `PATH`. Finding none is normal: on a
platform with no executor wheel, agentgauge uses Python.

`docs/PROTOCOL.md` specifies the wire form; `tests/conformance/` is the
normative definition of correct behaviour, and an implementation is
conformant when that suite passes against it.

```sh
cd rust && cargo build --release        # build the executor
cargo test --all && cargo clippy --all-targets -- -D warnings
cd .. && .venv/bin/pytest tests/conformance   # all implementations
.venv/bin/python bench/sandbox_bench.py       # compare them
```

The Rust executor is 10-40% faster per sandbox operation, which works out to
roughly 0.2% of a real run — model inference dominates. Its actual value is
streaming output, guaranteed container cleanup, and headroom at concurrency.
Measurements and caveats: `docs/BENCHMARKS.md`.

## Development

```sh
.venv/bin/python -m pytest tests/ -m "not docker"   # offline suite, no API keys
.venv/bin/python -m pytest tests/conformance        # the executor contract
.venv/bin/python -m pytest tests/                   # adds the Docker integration test
.venv/bin/python -m ruff check .
.venv/bin/python scripts/sandbox_smoke.py           # docker executor standalone check
.venv/bin/python scripts/single_run_smoke.py ollama/agentgauge-qwen
.venv/bin/python scripts/debug_loop.py              # agent loop w/o docker, prints every turn
```

CI runs ruff and the offline suite on every push.

## License

MIT — see `LICENSE`.
