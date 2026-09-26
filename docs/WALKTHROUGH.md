# Walkthrough

A guided tour of what happens when you run a benchmark, in the order the code
runs it. `README.md` says what the tool does and `CONTRIBUTING.md` says how to
work on it; this says **how it fits together**, so the next change lands in the
right place.

```sh
agentbench --tasks agentbench/tasks/example.yaml --model claude-haiku-4-5 --runs-per-task 3
```

## 1. The CLI decides what to run and what to run it with

`agentbench/cli.py`

Loads the task set into `TaskSet`, which **validates every task at load**.
`repo_url` and `repo_ref` reach a git command line on the *host*, so a task
file is an input-validation boundary rather than configuration
(`agentbench/models/task.py`, and `docs/SECURITY.md` for why).

`--repo` goes through `Task.pointed_at()` rather than assigning `repo_url`,
because `repo_id` is derived once at construction and is the key runs are
grouped by. Assigning directly filed every run under a repository the task was
no longer using.

`make_executor()` resolves `--executor {auto,rust,python}`. `auto` looks for a
binary (`sandbox/discovery.py`: `$AGENTBENCH_EXECUTOR` → installed package →
`PATH` → `rust/target`) and falls back to the in-process Python executor when
there is none. Finding none is normal — an sdist install has no binary.

Each invocation gets a `run_set_id`, and the report covers **only that
invocation** unless `--all-history` is passed. Reporting over all history
silently blended separate sessions into one pass rate.

## 2. The harness fans out

`agentbench/orchestrator/harness.py`

One coroutine per (task × model × repeat), capped by a semaphore. Two things
happen here that are easy to miss:

- **One provider per model**, shared across its runs, so concurrent runs reuse
  an HTTP client. Built up front, so a missing API key fails immediately
  rather than after every run has errored identically.
- **A throttled progress line per run.** A test suite's full output times four
  concurrent runs would be unreadable, so it reports at most one line every ten
  seconds — the first immediately.

## 3. One run, start to finish

`agentbench/orchestrator/runner.py` — `run_once()`. This is the spine.

| Phase | What happens | Timed as |
| --- | --- | --- |
| Materialize | `git clone` or `copytree` **on the host**, then check out `repo_ref` | `setup_s` |
| Start | Container created with the resource policy, repo uploaded | `setup_s` |
| Setup | `setup_commands` run in the sandbox | `setup_s` |
| Baseline | Full suite run to record which tests already fail | `setup_s` |
| Agent | The loop (section 4) | `agent_s` |
| Grade | Failing-test command, then the full suite again | `grading_s` |
| Inspect | Regressions, test tampering, the diff | `grading_s` |

Teardown is in a `finally`, and must be idempotent.

Three judgements live here rather than in the report:

- **A run that edits a test file is disqualified**, whatever its exit code.
  Grading is the exit code of one command, so without this a model that
  rewrote the test would score a pass.
- **Regressions come from a junit report, and "unavailable" is not "none".**
  A missing or unparseable report yields `None`, not `[]`. The report file is
  deleted before each pass, so a failed post-fix run cannot read back the
  baseline's.
- **Harness failures become `error`, not a failed run.** A Docker fault or a
  rate limit is not the model's doing.

## 4. The agent loop

`agentbench/agent_loop/loop.py`, tools in `tools.py`

Provider turn → execute tools → feed results back, until the model stops or
`max_turns`. Four tools: `read_file`, `write_file`, `edit`, `run_tests`.

Two behaviours worth knowing:

- **The no-edit nudge.** Weak models often describe a fix in prose and stop.
  Rather than ending the run, the loop prods them once more — and only a
  *successful* edit clears it, so a failed `old_string` match does not count.
- **`run_tests` streams.** Output is delivered as it is produced, so a
  five-minute suite is distinguishable from a hang. The tool accumulates what
  it streams, because the model still needs the whole output at the end — and
  an executor that streams leaves its result buffers empty.

Afterwards `agent_loop/transcript.py` turns the message history into a
`Transcript`: the turns, their tool calls, an excerpt of each result, and
whether the model had to be nudged. Everything is clipped — a full transcript
would be ~750KB per run — and **every clip records what it dropped**, because a
transcript that looks complete but isn't is worse than none.

## 5. The two seams

These carry most of the design, and changing behaviour at either one means
changing the contract.

**`SandboxExecutor`** (`agentbench/sandbox/executor.py`) is the only way
orchestration touches a container runtime. `docs/PROTOCOL.md` is its wire form;
**`tests/conformance/` is the normative definition of correct behaviour** —
a type signature cannot say "a timeout leaves the sandbox usable" or "teardown
is idempotent", and those are exactly what a reimplementation gets wrong.

Three implementations pass that suite:

| | Where | Notes |
| --- | --- | --- |
| `FakeExecutor` | `tests/fakes.py` | Real file semantics; declares no `process` capability, so process tests skip rather than pass by accident |
| `DockerSandboxExecutor` | `sandbox/docker_executor.py` | The reference implementation and the fallback |
| Rust `executord` | `rust/crates/` | Reached via `sandbox/subprocess_executor.py` over the protocol |

**`ModelProvider`** (`agentbench/providers/base.py`) is the only way the loop
touches a model API. Pricing lives in `providers/pricing.yaml`; a model with no
entry reports **no cost at all** rather than a figure derived from another
model's rate.

## 6. Storage and reporting

`agentbench/storage/db.py` — SQLite, run metadata only, never keys. Columns are
added by `_MIGRATIONS` so existing databases migrate in place; the 30 runs
recorded before any of this work still read correctly. Two columns needed the
table rebuilt to drop `NOT NULL`, because they carry a real *unknown*.

`agentbench/reporting/aggregate.py` → `html_report.py`. Grouped four ways:
per task × model, per model, **per repository**, and by difficulty. The repo
side is a *profile*, not a single score, and it **refuses to compare
repositories evaluated against different models** — otherwise a repo tested
only with a weak model looks hostile to agents when the difference is the
model.

Pass rates carry a 95% Wilson interval. At the small N a benchmark like this
runs, a bare percentage reads as far more precise than it is.

## The rule that explains most of the code

**Unknown is never collapsed into empty.**

| Field | `None` means | `[]` / `0` means |
| --- | --- | --- |
| `cost_usd` | no price on file for this model | genuinely free (a local model) |
| `regressions` | detection was unavailable | detection ran, found none |
| `tampered_tests` | the check never ran | it ran, nothing was touched |
| `transcript` | not recorded | recorded, no turns |
| `pass_rate` | every attempt errored | every valid attempt failed |

Each of these was a bug once, reporting a confident value it had not measured.
If you add a field that can be unknown, keep the distinction.

## What CI catches that a laptop cannot

Worth knowing before trusting a green local run. Four real defects came from
CI, not from local testing:

1. **Bare `pytest` vs `python -m pytest`** — the latter puts the working
   directory on `sys.path`, so modules importing `tests.fakes` collected fine
   locally and failed in CI.
2. **The Docker half of conformance** — no daemon on the machine at the time.
3. **A `time.monotonic()` assumption** — a progress sink treated `0.0` as
   "never printed". On a machine with days of uptime the first line always
   printed; on a fresh runner it was suppressed. *Invisible locally by
   construction.*
4. **Protocol framing across both implementations** — the sandbox image had
   been deleted locally.

The sandbox image is pinned by digest and its pytest by version, so the
environment a run happened in does not drift between builds.

## Where to look

| Looking for | Go to |
| --- | --- |
| The wire protocol | `docs/PROTOCOL.md` |
| Trust boundaries | `docs/SECURITY.md` |
| Executor measurements | `docs/BENCHMARKS.md` |
| What's left for v1 | `docs/ROADMAP.md` |
| Setup and expectations | `CONTRIBUTING.md` |
| The executor contract | `tests/conformance/` |
| Whether a task still means what it claims | `tests/test_task_integrity.py`, `tests/test_real_repo_tasks.py` |
