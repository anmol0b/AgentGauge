# Sandbox executor benchmarks

Reproduce with `python bench/sandbox_bench.py`. Needs Docker; needs no model
and no API key.

## Rust vs Python executor

Apple M-series, macOS, Docker Desktop 29.5.2, `agentbench-base:py3.12`,
**5 iterations**, milliseconds.

| operation | python median | python p90 | rust median | rust p90 | rust / python |
|---|---|---|---|---|---|
| start | 250.4 | 416.9 | 264.0 | 347.1 | 1.05x |
| exec (trivial) | 126.3 | 171.2 | 82.9 | 100.7 | **0.66x** |
| exec (1k lines out) | 45.4 | 51.4 | 41.2 | 43.6 | 0.91x |
| write 64 KB | 43.3 | 49.9 | 37.0 | 43.6 | 0.86x |
| read 64 KB | 8.8 | 10.6 | 5.3 | 6.2 | **0.60x** |
| teardown | 51.1 | 59.5 | 46.0 | 61.4 | 0.90x |

## What this says

**Rust is 10–40% faster on per-operation overhead, and that does not matter
much yet.**

The two clear wins are a trivial exec (0.66x) and a file read (0.60x) — the
operations where the work is almost entirely client-side overhead, which is
exactly where replacing a thread pool and a per-thread docker-py client with
native async should help.

`start` is unchanged (1.05x, inside the noise). It is dominated by Docker
creating and starting a container, which both executors ask the same daemon
to do. No amount of client-side work moves it.

Put that against a real run. The recorded runs average ~8 execs and one
container lifecycle each, so the Rust executor saves on the order of
**0.3 seconds per run** — against a mean run time of 157 seconds, or about
0.2%. Even against a fast API model at ~15s per run it is under 3%.

**So the case for the Rust executor is not speed.** It is:

- **streaming** — output arrives as produced instead of buffering until the
  command exits, which matters for a five-minute test run
- **container reaping** — one process owns every container it created and
  removes them on SIGTERM; a killed Python client cannot
- **headroom** — native async rather than a thread pool, which is where the
  per-operation gap would start to compound at high concurrency

That was the claim before measuring, and the measurement neither confirms a
speedup nor undermines the reasons. It does rule out describing this as a
performance rewrite.

## Caveats

- **n=5.** Enough to see a 40% difference, not enough to trust the ones near
  1.0x — `start`, `exec (1k lines)` and `teardown` have overlapping p90s and
  should be read as "no measured difference".
- One machine, one Docker version, macOS only. Docker Desktop on macOS runs
  containers in a VM, so absolute numbers will differ on native Linux; the
  ratios should hold better than the absolutes.
- Measures the sandbox in isolation. That is the point — an end-to-end run
  would mostly measure the model — but it means these numbers cannot be
  added up into an end-to-end claim beyond the rough arithmetic above.
