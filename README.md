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
├── providers/             # ModelProvider protocol + anthropic/openai impls
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

- Anthropic: `export ANTHROPIC_API_KEY=...`, use `--model claude-sonnet-4-5`
- OpenAI: `export OPENAI_API_KEY=...`, use `--model gpt-4o`
- Ollama (free, local, no key): `ollama pull qwen2.5-coder:14b`, then
  `--model ollama/qwen2.5-coder:14b`. Note: Ollama defaults to a 4096-token context;
  bake in a larger window (`ollama create <name> -f ollama/Modelfile`).

### Run

```sh
.venv/bin/python -m agentbench.cli \
  --tasks agentbench/tasks/example.yaml \
  --model ollama/agentbench-qwen \
  --runs-per-task 3 \
  --out report.html
```

Results are appended to `agentbench.db` (SQLite); the HTML report shows pass rate,
cost/latency mean±stddev per (task × model), individual runs, and the agent's diffs.

### Development

```sh
.venv/bin/python -m pytest tests/          # tool's own test suite
.venv/bin/python scripts/sandbox_smoke.py  # docker executor standalone check
.venv/bin/python scripts/single_run_smoke.py ollama/agentbench-qwen
.venv/bin/python scripts/debug_loop.py     # agent loop w/o docker, prints every turn
```
