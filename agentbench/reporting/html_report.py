"""Static HTML report rendering via Jinja2."""

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
  details { margin-bottom: 1rem; }
  pre { background: #f8f8f8; padding: 1rem; overflow-x: auto; }
</style></head><body>
<h1>AgentBench Report</h1>
<p>LLM outputs are non-deterministic: each task was run N times per model;
pass rate and standard deviations are reported explicitly.</p>

<h2>Variance summary (per task &times; model)</h2>
<table>
  <tr><th>Task</th><th>Model</th><th>Pass rate</th><th>Cost (mean &plusmn; std)</th>
      <th>Latency s (mean &plusmn; std)</th></tr>
  {% for v in report.variance %}
  <tr>
    <td>{{ v.task_id }}</td><td>{{ v.model }}</td>
    <td class="{{ 'pass' if v.pass_rate == 1.0 else 'fail' if v.pass_rate == 0 else '' }}">
      {{ "%.0f%%" | format(v.pass_rate * 100) }}</td>
    <td>${{ "%.4f" | format(v.cost_mean) }} &plusmn; {{ "%.4f" | format(v.cost_stddev) }}</td>
    <td>{{ "%.1f" | format(v.latency_mean) }} &plusmn; {{ "%.1f" | format(v.latency_stddev) }}</td>
  </tr>
  {% endfor %}
</table>

<h2>Individual runs</h2>
<table>
  <tr><th>Task</th><th>Model</th><th>Run</th><th>Passed</th><th>Cost</th>
      <th>Latency s</th><th>Tool calls</th><th>Tokens (in/out)</th><th>Regressions</th><th>Error</th></tr>
  {% for r in report.runs %}
  <tr>
    <td>{{ r.task_id }}</td><td>{{ r.model }}</td><td>{{ r.run_index + 1 }}</td>
    <td class="{{ 'pass' if r.passed else 'fail' }}">{{ "yes" if r.passed else "no" }}</td>
    <td>${{ "%.4f" | format(r.cost_usd) }}</td>
    <td>{{ "%.1f" | format(r.latency_s) }}</td>
    <td>{{ r.tool_call_count }}</td>
    <td>{{ r.tokens_in }}/{{ r.tokens_out }}</td>
    <td>{{ r.regressions | join(", ") if r.regressions else "—" }}</td>
    <td>{{ r.error or "—" }}</td>
  </tr>
  {% endfor %}
</table>

<h2>Diffs</h2>
{% for r in report.runs %}
<details>
  <summary>{{ r.task_id }} | {{ r.model }} | run {{ r.run_index + 1 }}
    ({{ "passed" if r.passed else "failed" }})</summary>
  <pre>{{ r.diff or "(no changes)" }}</pre>
</details>
{% endfor %}
</body></html>
""")


def render_html(report: AggregateReport, out_path: Path | str) -> Path:
    out = Path(out_path)
    out.write_text(_TEMPLATE.render(report=report))
    return out
