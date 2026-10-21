"""Provider selection and CLI argument parsing."""

import pytest

from agentgauge.cli import parse_args
from agentgauge.providers import make_provider
from agentgauge.providers.anthropic_provider import AnthropicProvider
from agentgauge.providers.openai_provider import OpenAIProvider


def test_claude_models_route_to_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(make_provider("claude-opus-5"), AnthropicProvider)


def test_gpt_models_route_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for model in ("gpt-4o", "o1-preview", "o3-mini"):
        assert isinstance(make_provider(model), OpenAIProvider)


def test_ollama_models_need_no_key_and_cost_nothing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = make_provider("ollama/qwen2.5-coder")
    assert isinstance(provider, OpenAIProvider)
    from agentgauge.models.run import TokenUsage

    assert provider.compute_cost(TokenUsage(tokens_in=10**6, tokens_out=10**6)) == 0.0


def test_gemini_models_route_to_googles_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    provider = make_provider("gemini-3.6-flash")
    assert isinstance(provider, OpenAIProvider)
    assert str(provider._client.base_url).startswith("https://generativelanguage.googleapis.com/")


def test_gemini_without_a_published_price_reports_no_cost(monkeypatch):
    """gemini-3.5-flash must not borrow 3.5-flash-lite's rate by prefix."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agentgauge.models.run import TokenUsage

    usage = TokenUsage(tokens_in=10**6, tokens_out=10**6)
    assert make_provider("gemini-3.5-flash").compute_cost(usage) is None
    assert make_provider("gemini-3.6-flash").compute_cost(usage) == 4.5


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="no provider for model"):
        make_provider("llama-3-70b")


def test_missing_key_is_reported_by_name(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_provider("claude-opus-5")


def _args(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentgauge", *argv])
    return parse_args()


def test_cli_defaults(monkeypatch):
    args = _args(["--tasks", "t.yaml", "--model", "claude-opus-5"], monkeypatch)
    assert args.model == ["claude-opus-5"]
    assert args.runs_per_task == 3
    assert args.all_history is False  # reporting on one invocation is the default


def test_cli_accepts_repeated_models(monkeypatch):
    args = _args(
        ["--tasks", "t.yaml", "--model", "claude-opus-5", "--model", "gpt-4o"], monkeypatch
    )
    assert args.model == ["claude-opus-5", "gpt-4o"]


def test_cli_requires_tasks_and_model(monkeypatch):
    with pytest.raises(SystemExit):
        _args(["--model", "claude-opus-5"], monkeypatch)
    with pytest.raises(SystemExit):
        _args(["--tasks", "t.yaml"], monkeypatch)


# --- executor selection ---------------------------------------------------
# An install with no Rust anywhere must still work: sdist builds, platforms
# with no wheel, and contributors who do not want the toolchain all land on
# the Python executor.


class _FakeDocker:
    """Stands in for DockerSandboxExecutor, whose __init__ pings the daemon."""


@pytest.fixture
def no_daemon(monkeypatch):
    monkeypatch.setattr("agentgauge.sandbox.docker_executor.DockerSandboxExecutor", _FakeDocker)


def _binary(monkeypatch, path):
    monkeypatch.setattr("agentgauge.cli.find_executor_binary", lambda: path)


def test_auto_prefers_the_binary_when_one_exists(monkeypatch, no_daemon, tmp_path):
    from agentgauge.cli import make_executor
    from agentgauge.sandbox.subprocess_executor import SubprocessExecutor

    _binary(monkeypatch, tmp_path / "agentgauge-executord")
    assert isinstance(make_executor("auto"), SubprocessExecutor)


def test_auto_falls_back_to_python_with_no_binary(monkeypatch, no_daemon):
    from agentgauge.cli import make_executor

    _binary(monkeypatch, None)
    assert isinstance(make_executor("auto"), _FakeDocker)


def test_python_is_used_even_when_a_binary_exists(monkeypatch, no_daemon, tmp_path):
    from agentgauge.cli import make_executor

    _binary(monkeypatch, tmp_path / "agentgauge-executord")
    assert isinstance(make_executor("python"), _FakeDocker)


def test_rust_without_a_binary_says_how_to_get_one(monkeypatch, no_daemon):
    """Silently falling back would hide that the choice was not honoured."""
    from agentgauge.cli import make_executor

    _binary(monkeypatch, None)
    with pytest.raises(RuntimeError, match="cargo build"):
        make_executor("rust")
