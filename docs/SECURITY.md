# Security

AgentGauge runs code written by a language model, and runs test suites from
repositories it was pointed at. That is the product, not an accident, so the
question is not whether untrusted code executes — it does — but what that
code can reach.

This document states the trust boundaries plainly, including the ones that
are weaker than they look.

## Reporting a vulnerability

Open a [private security advisory][advisory] on the repository. Please do not
open a public issue for anything exploitable.

[advisory]: https://github.com/anmol0b/AgentGauge/security/advisories/new

## What is trusted, and what is not

| Input | Trust | Where it runs |
|---|---|---|
| Model-generated code and commands | **Untrusted** | Inside the sandbox container |
| The repository under test | **Untrusted** | Inside the sandbox container |
| Task files (`--tasks`) | **Trusted** | Partly on the **host** |
| `--image` | **Trusted** | Host Docker daemon |
| API keys | Secret | Host process memory only |

The asymmetry matters: the model's output is contained, but **a task file is
not**. Treat a task set like a shell script you are about to run.

## The sandbox

Agent commands run in a container with:

- **no network** (`network_mode=none`) — this is the main containment, and the
  reason a compromised agent cannot phone home or fetch a payload. The agent
  loop calls model APIs from the *host*, so the sandbox never needs egress.
- memory, CPU and PID limits (`agentgauge/sandbox/policy.py`)
- a per-run container, removed at teardown

**A container is not a security boundary against a determined attacker.** It
contains mistakes and ordinary misbehaviour well; it does not reliably contain
a kernel exploit or a container escape. If you are benchmarking a model you
have reason to distrust, or task repositories you have not read, run the whole
thing on a disposable machine or in CI rather than on a workstation that holds
anything you care about.

Commands run as root inside the container. That is root in a namespace, not on
the host, but it means the image itself is part of your trust boundary — build
`--image` from something you trust.

## Task files are executed host-side

Before any sandbox exists, `_materialize_repo` acts on the task file's
`repo_url` on the host: `git clone` for a remote, `shutil.copytree` for a
local path. A hostile task file is therefore closer to arbitrary code
execution than to configuration.

What is validated at load (`agentgauge/models/task.py`):

- `repo_url` may not begin with `-`, which git reads as an option
- `repo_url` transports are limited to https, http, ssh, git and file;
  `ext::`, which makes git run an arbitrary command, is refused
- `repo_ref` must be a plausible ref — a sha, branch, tag or `refs/...` path.
  This matters more than it looks: `git checkout -- <ref>` means "these
  paths", so a ref cannot be separated from options by `--`, and an
  unvalidated `--orphan` reaches checkout as an option and changes the
  repository state. That was confirmed by trying it.

What is **not** defended, by design:

- A local `repo_url` copies whatever directory it names into the sandbox. A
  task pointing at `~/.ssh` puts those files where the model can read them.
  With no network the model cannot send them anywhere, but it can write them
  into files that end up in the captured diff and therefore in the HTML
  report. Restricting this would break the ordinary case of benchmarking a
  repository elsewhere on disk, so it is documented rather than blocked.
- `setup_commands`, `failing_test_command` and `full_test_command` are
  arbitrary shell. They run **inside** the sandbox, which is the intent.

**So: only run task sets you would run as a script.** This is the sharpest
edge in the tool, and it gets sharper the moment task sets are shared.

## API keys

Read from environment variables only, held in process memory, and never
written to the database, the HTML report or any log. `agentgauge.db` stores
run metadata only. The sandbox has no network and never sees a key.

## The HTML report

The report renders diffs, file paths and test ids that the model under test
wrote. The template escapes output (`autoescape=True`), so a diff containing
`</pre><script>` is inert. The report is still model-influenced content —
treat it as you would any untrusted document, and do not serve it from a
domain where a mistake would be costly.

## Executor binaries

`--executor auto` resolves a binary from `$AGENTGAUGE_EXECUTOR`, the installed
package, `PATH`, then `rust/target`. An attacker who can write to any of those
controls the sandbox. That is the same trust model as any tool that finds
helpers on `PATH`; it is listed here because the executor is the component
that holds the isolation.

## Scope

Reports we consider in scope:

- escaping the sandbox from agent-generated code
- a task file achieving host code execution beyond the documented
  clone-and-copy behaviour
- API keys reaching disk, the report or the database
- a crafted model response causing host code execution

Out of scope:

- a model writing bad code inside the sandbox — that is the measurement
- anything requiring a hostile task file, which is documented as trusted
- container escapes in Docker itself, which belong upstream
