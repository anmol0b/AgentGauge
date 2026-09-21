"""Static HTML report rendering via Jinja2.

autoescape is on: diffs and file paths in this report are written by the model
under test, so they are untrusted input to the template.
"""

from pathlib import Path

from jinja2 import Template

from agentbench.models.report import AggregateReport

_TEMPLATE = Template("""\
<!doctype html>
<html><head><meta charset="utf-8"><title>AgentBench Report</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 2rem; color: #222; }
  table { border-collapse: collapse; margin-bottom: 2rem; }
  th, td { border: 1px solid #ccc; padding: 6px 12px; text-align: left; }
  th { background: #f4f4f4; }
  .pass { color: #0a7a0a; font-weight: 600; }
  .fail { color: #b00; font-weight: 600; }
  .warn { color: #a05a00; font-weight: 600; }
  .muted { color: #666; }
  details { margin-bottom: 1rem; }
  pre { background: #f8f8f8; padding: 1rem; overflow-x: auto; }
  .caveats { background: #fffbea; border: 1px solid #e8d48b; padding: 0.5rem 1rem;
             margin-bottom: 2rem; }
  .caveats li { margin: 0.35rem 0; }
</style></head><body>
<h1>AgentBench Report</h1>

<div class="caveats">
<strong>How to read these numbers</strong>
<ul>
  <li>LLM outputs are non-deterministic: each task ran N times per model, and
      standard deviations are reported rather than hidden. With small N a pass
      rate is a coarse estimate &mdash; the valid-run count is shown alongside it.</li>
  <li><strong>Pass rate excludes harness errors.</strong> A Docker failure, rate
      limit or bad setup command is not a model failure; those are counted
      separately as the error rate.</li>
  <li><strong>Runs that modified test files are disqualified</strong> and never
      count as a pass, regardless of exit code. Runs marked
      &ldquo;not checked&rdquo; predate this check or errored before it ran &mdash;
      their pass/fail is unverified on this point.</li>
  <li><strong>Cost is only reported for models with a price on file</strong>
      (<code>agentbench/providers/pricing.yaml</code>). An unlisted model shows
      &ldquo;unpriced&rdquo; rather than a figure guessed from another model's rate.</li>
  <li><strong>Regressions are detected from a junit report</strong>, which
      needs a pytest-compatible suite. Where that report is missing or
      unparseable the run reads &ldquo;not detected&rdquo; &mdash; not
      &ldquo;none&rdquo;.</li>
  {% if concurrency %}
  <li><strong>Latency is not a clean measurement.</strong> Runs executed
      {{ concurrency }}-way concurrently against resource-capped containers, so
      wall-clock times include scheduling contention and are not comparable
      across different concurrency settings.</li>
  {% endif %}
</ul>
</div>

<h2>Variance summary (per task &times; model)</h2>
<table>
  <tr><th>Task</th><th>Model</th><th>Pass rate</th><th>Valid runs</th>
      <th>Harness errors</th><th>Disqualified</th>
      <th>Cost (mean &plusmn; std)</th><th>Latency s (mean &plusmn; std)</th></tr>
  {% for v in report.variance %}
  <tr>
    <td>{{ v.task_id }}</td><td>{{ v.model }}</td>
    {% if v.pass_rate is none %}
    <td class="muted">n/a</td>
    {% else %}
    <td class="{{ 'pass' if v.pass_rate == 1.0 else 'fail' if v.pass_rate == 0 else '' }}">
      {{ "%.0f%%" | format(v.pass_rate * 100) }}</td>
    {% endif %}
    <td>{{ v.n_valid }} / {{ v.n_runs }}</td>
    <td class="{{ 'warn' if v.error_rate else '' }}">
      {{ "%.0f%%" | format(v.error_rate * 100) }}</td>
    <td class="{{ 'warn' if v.tampered_count else '' }}">
      {{ v.tampered_count or "—" }}</td>
    {% if v.cost_mean is none %}
    <td class="muted">unpriced</td>
    {% else %}
    <td>${{ "%.4f" | format(v.cost_mean) }} &plusmn; {{ "%.4f" | format(v.cost_stddev) }}</td>
    {% endif %}
    <td>{{ "%.1f" | format(v.latency_mean) }} &plusmn; {{ "%.1f" | format(v.latency_stddev) }}</td>
  </tr>
  {% endfor %}
</table>

<h2>Individual runs</h2>
<table>
  <tr><th>Task</th><th>Model</th><th>Run</th><th>Passed</th><th>Stopped because</th>
      <th>Cost</th><th>Latency s</th><th>Tool calls</th><th>Tokens (in/out)</th>
      <th>Regressions</th><th>Edited tests</th><th>Harness error</th></tr>
  {% for r in report.runs %}
  <tr>
    <td>{{ r.task_id }}</td><td>{{ r.model }}</td><td>{{ r.run_index + 1 }}</td>
    {% if r.error %}
    <td class="muted">errored</td>
    {% else %}
    <td class="{{ 'pass' if r.passed else 'fail' }}">{{ "yes" if r.passed else "no" }}</td>
    {% endif %}
    <td>{{ r.stop_reason or "—" }}</td>
    {% if r.cost_usd is none %}
    <td class="muted">unpriced</td>
    {% else %}
    <td>${{ "%.4f" | format(r.cost_usd) }}</td>
    {% endif %}
    <td>{{ "%.1f" | format(r.latency_s) }}</td>
    <td>{{ r.tool_call_count }}</td>
    <td>{{ r.tokens_in }}/{{ r.tokens_out }}</td>
    {% if r.regressions %}
    <td class="warn">{{ r.regressions | join(", ") }}</td>
    {% elif r.regressions is none %}
    <td class="muted">not detected</td>
    {% else %}
    <td>—</td>
    {% endif %}
    {% if r.tampered_tests %}
    <td class="warn">{{ r.tampered_tests | join(", ") }}</td>
    {% elif r.tampered_tests is none %}
    <td class="muted">not checked</td>
    {% else %}
    <td>—</td>
    {% endif %}
    <td>{{ r.error or "—" }}</td>
  </tr>
  {% endfor %}
</table>

<h2>Diffs</h2>
{% for r in report.runs %}
<details>
  <summary>{{ r.task_id }} | {{ r.model }} | run {{ r.run_index + 1 }}
    ({{ "errored" if r.error else "passed" if r.passed else "failed" }}{{
       ", disqualified: edited tests" if r.tampered_tests else "" }})</summary>
  <pre>{{ r.diff or "(no changes)" }}</pre>
</details>
{% endfor %}
</body></html>
""", autoescape=True)


def render_html(
    report: AggregateReport, out_path: Path | str, concurrency: int | None = None
) -> Path:
    out = Path(out_path)
    out.write_text(_TEMPLATE.render(report=report, concurrency=concurrency))
    return out
