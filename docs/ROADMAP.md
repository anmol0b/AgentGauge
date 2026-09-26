# What's left for v1

Kept in the repo so it can be corrected in the same pull request as the work
it describes. Sections are split by **who can do the item**, because that is
the real constraint: one group cannot be tested without an API key, one is
repairing behaviour that already exists, one is new capability.

## What v1 claims to be

Writing it down makes every item below an in-or-out decision rather than an
argument:

> **v1 is a self-hosted CLI that reports trustworthy numbers for coding-agent
> performance on real repositories, and installs with `pip install`.**

Three words carry the scope:

- **trustworthy** — every reported figure has been produced at least once by
  the path that will produce it for users. Anything never executed end to end
  is not shippable, however well unit-tested.
- **real repositories** — tasks from repos with dependencies, not only
  zero-dependency ones chosen because they are easy to package.
- **`pip install`** — published, the Rust binary arriving automatically, and a
  working fallback when it cannot.

---

## Needs an API key to test

The code exists and is unit-tested. What it needs is a key to be **trusted**,
because the path has never run. One cheap run resolves the whole section:
seven tasks against `claude-haiku-4-5` at one run each costs cents.

Every recorded run is local Ollama at `(0.0, 0.0)` pricing, so **`cost_usd`
has never been a non-zero number outside a unit test**.

| Never executed | What breaks if it is wrong |
| --- | --- |
| Cache-token accounting | `cache_creation_input_tokens` / `cache_read_input_tokens` are read and billed at their own rates. No real Anthropic response has been parsed, so the field names *and* the arithmetic are unverified. |
| The whole OpenAI provider | Covered only by message-conversion tests. |
| Rate-limit handling | `error_rate` exists so a 429 does not look like a model failure. No 429 has ever occurred. |
| `max_tokens` stop reason | Mapped in both providers, never observed. |
| Token accounting over a long run | Usage totals and cost accumulation across 25 turns, with a model that uses them. |
| Pricing entries | The lookup is tested; the rates themselves are transcribed from docs. |

**Difficulty labels are a guess.** The real-repo tasks are verified
*solvable* — CI applies the upstream fix and the test passes — but nothing has
verified they are *hard*. `medium` and `hard` are a judgement of the diff, not
a measurement, and difficulty is what the report segments by.

## Fixes

Defects and gaps in behaviour that already exists. None needs a key.

### Worth doing for v1

- **Task-set breadth.** Two tasks, both from boltons. That is not a benchmark,
  and the concentration is a live risk: in September a single upstream PR's
  commit structure broke one task, and would have broken the suite if both had
  come from that PR. Target five to eight tasks across three or more repos.
- **A task from a repo with dependencies.** Both current tasks are boltons
  *because* it needs no task-specific image — so the per-task-image recipe in
  `agentbench/tasks/real_repos.yaml` has never been followed. Unverified
  instructions in a contributor-facing file are a liability.
- **Benchmark rigour.** `docs/BENCHMARKS.md` is n=5, on one macOS machine
  where Docker runs containers in a VM. Ratios near 1.0x are noise and the doc
  says so; Linux in CI with higher N would make it defensible rather than
  caveated.
- **Coverage floor is 75, not 80.** Lowered honestly when `docker_executor.py`
  grew and stayed unreachable offline. Merging coverage across the offline and
  sandbox jobs restores it without hiding anything.

### Known, documented, deliberately unfixed

In `README.md` under Limitations. Listed here so the decision gets revisited
on purpose rather than forgotten:

- The full suite runs **twice** per task run (baseline, then after the fix) for
  regression detection. Roughly doubles wall-clock on slow suites.
- A model that never edits can burn all 25 turns being nudged, inflating its
  recorded cost and tokens. A cap on nudges would fix it.
- A local `repo_url` copies whatever directory it names into the sandbox. Not
  blocked, because blocking it would break benchmarking a repo elsewhere on
  disk. See `docs/SECURITY.md`.

## Additions

New capability. Deliberately short — v1 is about the numbers being
trustworthy, not about surface area.

- **Publishing.** The release workflow builds four wheels and the sdist then
  stops. v1 needs the PyPI project, trusted publishing, and
  `agentbench-executord` declared as an extra so `pip install agentbench[rust]`
  resolves.
- **Results out of the tool.** The HTML report is for humans, SQLite is for
  the tool. Anyone doing their own analysis has to query the schema; a `--json`
  output or `agentbench export` removes that.
- *Worth considering, not required:* `agentbench validate <tasks.yaml>` (check
  a task set without running it); trend across run sets, which is mostly a
  reporting change since every run already carries a `run_set_id` and a
  timestamp.

## Not v1

- **A native Linux backend** (youki, runc, namespaces + cgroups). The only
  thing that would fix the timeout-kill limitation — Docker's Engine API
  cannot kill a running exec, so both executors wrap commands in
  `timeout --signal=KILL` — and the only thing that would make container start
  meaningfully faster. Linux-only, so it means two backends plus Docker for
  development on a Mac. The `SandboxExecutor` seam exists so this can be added
  without touching evaluation logic.
- **A hosted service.** Multi-tenant isolation for untrusted model-generated
  code is a different product with a different risk profile.
- **Moving anything else into Rust.** The agent loop is I/O-bound on model
  APIs; aggregation handles trivial volumes. Measured: the Rust executor is
  10–40% faster per sandbox operation, which is ~0.2% of a real run. Widening
  the Rust surface needs a measurement, and there isn't one.
- **More protocol churn.** 2.0 removed the message-size cliff, made
  capabilities meaningful, and dropped two fields nothing honoured. Further
  breaking changes get much more expensive once anyone else has written an
  executor.

## Order

The dependencies are lopsided: one cheap action unblocks the largest category,
and one non-engineering decision unblocks two more.

| # | Do this | Why here |
| --- | --- | --- |
| 1 | **One paid run** — 7 tasks, `claude-haiku-4-5`, 1 run each | Costs cents and resolves the entire API-key section. Also tells you whether the difficulty labels mean anything. |
| 2 | **Make the repo public** (or Pro) | Branch protection and rulesets return `403 Upgrade to GitHub Pro or make this repository public`. Every check runs on every PR and **none of them block a merge**. Also a prerequisite for open-sourcing. |
| 3 | **Task-set breadth** | The gap between "works" and "worth running". Mostly finding commits; the recipe and the CI check exist. |
| 4 | **Publish to PyPI** | Wants a stable task set and a real cost figure behind it, so after 1 and 3. |
| 5 | Benchmark rigour, coverage floor, per-task-image verification | Housekeeping. Real, but none of it blocks the v1 sentence. |

**Step 2 is the cheapest risk reduction available.** Every safeguard in this
repo is currently opt-out by accident, because a red PR still merges. CI has
caught four things local runs structurally could not — see
`docs/WALKTHROUGH.md` — and it is still advisory.
