"""Provider selection and CLI argument parsing."""

import pytest

from agentbench.cli import parse_args
from agentbench.providers import make_provider
from agentbench.providers.anthropic_provider import AnthropicProvider
from agentbench.providers.openai_provider import OpenAIProvider


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
    from agentbench.models.run import TokenUsage

    assert provider.compute_cost(TokenUsage(tokens_in=10**6, tokens_out=10**6)) == 0.0


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="no provider for model"):
        make_provider("llama-3-70b")


def test_missing_key_is_reported_by_name(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_provider("claude-opus-5")


def _args(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentbench", *argv])
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
