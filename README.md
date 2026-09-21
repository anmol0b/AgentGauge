# PersonaFit

## AgentBench — AI-friendliness benchmark

A CLI that takes a repo + a set of "fix this failing test" tasks, runs an AI coding
agent against each task inside an isolated Docker sandbox, and reports success rate,
tokens, cost, latency, tool-call count, and regressions — with N repeated runs per
task to report variance (LLM outputs are non-deterministic; variance is reported
explicitly, never hidden).

### Architecture

Orchestration never talks to Docker directly — all sandbox interaction goes through
the `SandboxExecutor` protocol (`agentbench/sandbox/executor.py`), so the executor can
be swapped (e.g. rewritten in Rust) without touching eval logic. Providers sit behind
the `ModelProvider` protocol (`agentbench/providers/base.py`); Anthropic and OpenAI are
two implementations of the same interface, plus OpenAI-compatible endpoints (Ollama,
Groq, ...) via `base_url`. All cross-module data is typed Pydantic v2 models. API keys
are read from env vars only, held in memory, never persisted.

```
agentbench/
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

### Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker build -t agentbench-base:py3.12 -f docker/base.Dockerfile docker/
```

Sandboxed containers run with `network_mode=none` (the agent loop calls model APIs
from the host), so test dependencies must be baked into the image.

### Models

- Anthropic: `export ANTHROPIC_API_KEY=...`, use `--model claude-sonnet-5`
- OpenAI: `export OPENAI_API_KEY=...`, use `--model gpt-4o`
- Ollama (free, local, no key): pull a model, set `FROM` in `ollama/Modelfile` to
  match, `ollama create agentbench-qwen -f ollama/Modelfile`, then
  `--model ollama/agentbench-qwen`. The Modelfile exists to raise Ollama's default
  4096-token context, which is too small for an agent loop.

One provider is built per model and shared across that model's runs, so a
missing API key fails immediately rather than after every run has errored.

Costs come from `agentbench/providers/pricing.yaml`. A model with no entry there
reports **no cost at all** rather than a figure guessed from another model's rate —
add an entry when you benchmark a new model.

### Run

```sh
.venv/bin/agentbench \
  --tasks agentbench/tasks/example.yaml \
  --model ollama/agentbench-qwen \
  --runs-per-task 3 \
  --out report.html
```

Results are appended to `agentbench.db` (SQLite). Each invocation gets a run-set id
and the report covers **only that invocation** — pass `--all-history` to aggregate
every run in the database instead (which mixes separate sessions into one pass rate).

### Scoring rules

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
- **Regressions come from a junit report**, which needs a pytest-compatible suite.
  Where it is missing or unparseable, runs read "not detected", not "none".

### Tasks

`agentbench/tasks/example.yaml` holds seeded-bug tasks over local fixture repos —
fast, offline, and useful for smoke-testing the harness. Difficulty runs from
one-line fixes to `pagination-partial-page-bug`, where the obvious fix passes the
failing test and breaks two passing ones.

They are toy repos. A high pass rate on them is not evidence a model is good at real
codebases; `agentbench/tasks/real_repos.example.yaml` documents how to build tasks
from real upstream bugfix commits (pin `repo_ref`, or the task stops being
reproducible the moment the branch moves).

### Limitations

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

### Development

```sh
.venv/bin/python -m pytest tests/ -m "not docker"   # offline suite, no API keys
.venv/bin/python -m pytest tests/                   # adds the Docker integration test
.venv/bin/python -m ruff check .
.venv/bin/python scripts/sandbox_smoke.py           # docker executor standalone check
.venv/bin/python scripts/single_run_smoke.py ollama/agentbench-qwen
.venv/bin/python scripts/debug_loop.py              # agent loop w/o docker, prints every turn
```

CI runs ruff and the offline suite on every push.

### License

MIT — see `LICENSE`.
