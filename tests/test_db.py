from agentbench.models.run import RunResult
from agentbench.storage.db import RunStore


def _result(**overrides):
    base = dict(
        task_id="t1", run_index=0, model="claude-sonnet-4-5", passed=True,
        tokens_in=100, tokens_out=50, cost_usd=0.01, latency_s=12.0,
        tool_call_count=3, diff="+fix", regressions=["pkg::test_broke"],
        error=None,
    )
    return RunResult(**(base | overrides))


def test_round_trip(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    store.insert_many([_result(), _result(run_index=1, passed=False, error="boom")])
    store.close()

    reopened = RunStore(tmp_path / "runs.db")
    runs = reopened.all_runs()
    reopened.close()

    assert len(runs) == 2
    assert runs[0] == _result()
    assert runs[1].passed is False
    assert runs[1].error == "boom"
    assert runs[1].regressions == ["pkg::test_broke"]
