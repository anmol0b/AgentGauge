"""Static HTML report rendering via Jinja2.

autoescape is on: diffs, file paths and test ids in this report are written
by the model under test, so they are untrusted input to the template.
"""

from pathlib import Path

from jinja2 import Template

from agentbench.models.report import AggregateReport

_TEMPLATE = Template("""\
<!doctype html>
<html><head><meta charset="utf-8"><title>AgentBench Report</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 2rem; color: #222;
         max-width: 1200px; }
  table { border-collapse: collapse; margin-bottom: 2rem; font-variant-numeric: tabular-nums; }
  th, td { border: 1px solid #ccc; padding: 6px 12px; text-align: left; }
  th { background: #f4f4f4; }
  .pass { color: #0a7a0a; font-weight: 600; }
  .fail { color: #b00; font-weight: 600; }
  .warn { color: #a05a00; font-weight: 600; }
  .muted { color: #666; }
  .ci { color: #666; font-weight: 400; font-size: 0.9em; }
  details { margin-bottom: 1rem; }
  pre { background: #f8f8f8; padding: 1rem; overflow-x: auto; }
  .caveats { background: #fffbea; border: 1px solid #e8d48b; padding: 0.5rem 1rem;
             margin-bottom: 2rem; }
  .caveats li { margin: 0.35rem 0; }
  h2 { margin-top: 2.5rem; }
</style></head><body>
<h1>AgentBench Report</h1>

<div class="caveats">
<strong>How to read these numbers</strong>
<ul>
  <li>LLM outputs are non-deterministic: each task ran N times per model.
      Pass rates carry a <strong>95% confidence interval</strong> — at the small
      N a benchmark like this runs, 3/3 is not &ldquo;100%&rdquo;, it is
      &ldquo;somewhere above about 44%&rdquo;. Overlapping intervals mean the
      models are not separated by this data.</li>
  <li><strong>Pass rate excludes harness errors.</strong> A Docker failure, rate
      limit or bad setup command is not a model failure; those are counted
      separately as the error rate. If every attempt errored, pass rate is
      &ldquo;n/a&rdquo;, never 0%.</li>
  <li><strong>Runs that modified test files are disqualified</strong> and never
      count as a pass, regardless of exit code. Runs marked
      &ldquo;not checked&rdquo; predate this check or errored before it ran.</li>
  <li><strong>Cost is only reported for models with a price on file</strong>
      (<code>agentbench/providers/pricing.yaml</code>). An unlisted model shows
      &ldquo;unpriced&rdquo; rather than a figure guessed from another model's
      rate, and a model total is withheld if any of its runs was unpriced.</li>
  <li><strong>Regressions are detected from a junit report</strong>, which needs
      a pytest-compatible suite. Where that report is missing or unparseable the
      run reads &ldquo;not detected&rdquo; &mdash; not &ldquo;none&rdquo;.</li>
  {% if concurrency %}
  <li><strong>Latency is not a clean measurement.</strong> Runs executed
      {{ concurrency }}-way concurrently against resource-capped containers, so
      wall-clock times include scheduling contention and are not comparable
      across different concurrency settings.</li>
  {% endif %}
</ul>
</div>

{% macro rate(value, ci, klass=true) -%}
  {%- if value is none -%}
    <span class="muted">n/a</span>
  {%- else -%}
    <span class="{{ ('pass' if value == 1.0 else 'fail' if value == 0 else '') if klass else '' }}"
      >{{ "%.0f%%" | format(value * 100) }}</span>
    {%- if ci %} <span class="ci">({{ "%.0f" | format(ci.low * 100) }}&ndash;{{
      "%.0f" | format(ci.high * 100) }})</span>{% endif -%}
  {%- endif -%}
{%- endmacro %}

{% macro stat(s, fmt="%.1f") -%}
  {%- if s.mean is none -%}<span class="muted">—</span>
  {%- else -%}{{ fmt | format(s.mean) }} &plusmn; {{ fmt | format(s.stddev) }}{%- endif -%}
{%- endmacro %}

<h2>Models</h2>
<p class="muted">Every task, per model. Pass rate shows the 95% interval in
parentheses.</p>
<table>
  <tr><th>Model</th><th>Pass rate (95% CI)</th><th>Valid runs</th>
      <th>Harness errors</th><th>Disqualified</th><th>Caused regressions</th>
      <th>Total cost</th><th>Mean latency s</th><th>Tool calls</th>
      <th>Tokens in</th><th>Tokens out</th></tr>
  {% for m in report.models %}
  <tr>
    <td><strong>{{ m.model }}</strong></td>
    <td>{{ rate(m.pass_rate, m.pass_rate_ci) }}</td>
    <td>{{ m.n_valid }} / {{ m.n_runs }}</td>
    <td class="{{ 'warn' if m.error_rate else '' }}">{{
      "%.0f%%" | format(m.error_rate * 100) }}</td>
    <td class="{{ 'warn' if m.tampered_count else '' }}">{{ m.tampered_count or "—" }}</td>
    <td class="{{ 'warn' if m.regressed_count else '' }}">{{ m.regressed_count or "—" }}</td>
    {% if m.total_cost is none %}
    <td class="muted">unpriced{% if m.n_unpriced %} ({{ m.n_unpriced }} runs){% endif %}</td>
    {% else %}
    <td>${{ "%.4f" | format(m.total_cost) }}</td>
    {% endif %}
    <td>{{ "%.1f" | format(m.latency_mean) if m.latency_mean is not none else "—" }}</td>
    <td>{{ stat(m.tool_calls) }}</td>
    <td>{{ stat(m.tokens_in, "%.0f") }}</td>
    <td>{{ stat(m.tokens_out, "%.0f") }}</td>
  </tr>
  {% endfor %}
</table>

{% if report.by_difficulty %}
<h2>By difficulty</h2>
<table>
  <tr><th>Model</th><th>Difficulty</th><th>Pass rate (95% CI)</th><th>Valid runs</th></tr>
  {% for d in report.by_difficulty %}
  <tr>
    <td>{{ d.model }}</td><td>{{ d.difficulty }}</td>
    <td>{{ rate(d.pass_rate, none) }}</td><td>{{ d.n_valid }}</td>
  </tr>
  {% endfor %}
</table>
{% endif %}

<h2>Variance summary (per task &times; model)</h2>
<table>
  <tr><th>Task</th><th>Model</th><th>Pass rate (95% CI)</th><th>Valid runs</th>
      <th>Harness errors</th><th>Disqualified</th><th>Regressions</th>
      <th>Cost (mean &plusmn; std)</th><th>Latency s (mean &plusmn; std)</th>
      <th>Tool calls</th></tr>
  {% for v in report.variance %}
  <tr>
    <td>{{ v.task_id }}</td><td>{{ v.model }}</td>
    <td>{{ rate(v.pass_rate, v.pass_rate_ci) }}</td>
    <td>{{ v.n_valid }} / {{ v.n_runs }}</td>
    <td class="{{ 'warn' if v.error_rate else '' }}">{{
      "%.0f%%" | format(v.error_rate * 100) }}</td>
    <td class="{{ 'warn' if v.tampered_count else '' }}">{{ v.tampered_count or "—" }}</td>
    <td class="{{ 'warn' if v.regressed_count else '' }}">{{ v.regressed_count or "—" }}</td>
    {% if v.cost_mean is none %}
    <td class="muted">unpriced</td>
    {% else %}
    <td>${{ "%.4f" | format(v.cost_mean) }} &plusmn; {{ "%.4f" | format(v.cost_stddev) }}</td>
    {% endif %}
    <td>{{ "%.1f" | format(v.latency_mean) if v.latency_mean is not none else "—" }}
        &plusmn; {{ "%.1f" | format(v.latency_stddev) }}</td>
    <td>{{ stat(v.tool_calls) }}</td>
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
