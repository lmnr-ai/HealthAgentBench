"""Unit tests for the Pi harness's host-side half.

Pi itself runs in the sandbox and needs Docker, a model key and ~20 minutes, so
none of that is exercised here. What is exercised is everything that decides
whether the resulting trajectory is *usable*: the trace-continuation env the
extension needs, the counters we read back out of Pi's event stream, and the
fact that a pi record is the same record as a bash record with a different
``harness``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.harbor_agents.pi_agent import (  # noqa: E402
    PI_SUMMARY_SCRIPT,
    LaminarPiAgent,
)


class _ExecResult:
    def __init__(self, stdout: str = "", stderr: str = "", return_code: int = 0):
        self.stdout, self.stderr, self.return_code = stdout, stderr, return_code


class _FakeEnvironment:
    def __init__(self, task_dir: Path):
        self.environment_dir = task_dir / "environment"

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        return _ExecResult()


class _FinalizeEnvironment:
    """Answers `_finalize`'s two reads: the submission `cat` and the summary."""

    SUMMARY: ClassVar[dict] = {
        "steps": 3,
        "tool_calls": 5,
        "usage": {"input": 10, "output": 2, "cached": 0},
        "cost": 0.0,
        "stop_reason": "stop",
        "error": "",
    }

    def __init__(
        self,
        task_dir: Path,
        submission: str = "",
        read_error: BaseException | None = None,
    ):
        self.environment_dir = task_dir / "environment"
        self.submission = submission
        self.read_error = read_error

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        if command.startswith("cat "):
            if self.read_error is not None:
                raise self.read_error
            return _ExecResult(stdout=self.submission)
        return _ExecResult(stdout=json.dumps(self.SUMMARY))


class _RecordedLaminar:
    """Stand-in for the SDK, so a record can be inspected without a project."""

    def __init__(self):
        self.metadata: dict = {}
        self.output = None

    def set_span_output(self, value):
        self.output = value

    def set_trace_metadata(self, metadata):
        self.metadata = metadata

    def get_trace_id(self):
        return "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def laminar(monkeypatch) -> _RecordedLaminar:
    stub = _RecordedLaminar()
    monkeypatch.setattr("scripts.harbor_agents.trajectory.Laminar", stub)
    return stub


@pytest.fixture
def agent(tmp_path) -> LaminarPiAgent:
    return LaminarPiAgent(
        logs_dir=tmp_path,
        model_name="anthropic/claude-sonnet-5",
        logger=logging.getLogger("test"),
        lmnr_key_var="HAB_LMNR_PROJECT_API_KEY",
        extra_env={"HAB_LMNR_PROJECT_API_KEY": "test-key"},
    )


PI_MODELS = {
    "providers": {
        "azure-foundry": {
            "baseUrl": "https://example.invalid/openai/v1",
            "api": "openai-responses",
            "apiKey": "AZURE_API_KEY",
            "authHeader": True,
            "models": [{"id": "gpt-5.6-terra", "reasoning": True}],
        }
    }
}


def _pi_agent(tmp_path, **kwargs) -> LaminarPiAgent:
    return LaminarPiAgent(
        logs_dir=tmp_path,
        logger=logging.getLogger("test"),
        lmnr_key_var="HAB_LMNR_PROJECT_API_KEY",
        extra_env={"HAB_LMNR_PROJECT_API_KEY": "test-key"},
        **kwargs,
    )


def _assistant(usage: dict, tool_calls: int = 0, stop: str = "stop") -> str:
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": stop,
                "usage": usage,
                "content": [{"type": "text", "text": "..."}]
                + [
                    {"type": "toolCall", "id": str(i), "name": "bash", "arguments": {}}
                    for i in range(tool_calls)
                ],
            },
        }
    )


def _summarize(path: Path) -> dict:
    """Run the in-sandbox reducer exactly as the agent runs it."""
    proc = subprocess.run(
        [sys.executable, "-c", PI_SUMMARY_SCRIPT, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_summary_counts_turns_tools_and_tokens(tmp_path: Path):
    """`num_steps` is turns, not events -- pi emits several events per turn."""
    stream = tmp_path / "pi.txt"
    stream.write_text(
        "\n".join(
            [
                json.dumps({"type": "agent_start"}),
                _assistant({"input": 100, "output": 20, "cacheRead": 5}, tool_calls=2),
                json.dumps({"type": "tool_execution_end"}),
                # A user/toolResult message must not count as a turn.
                json.dumps({"type": "message_end", "message": {"role": "toolResult"}}),
                _assistant(
                    {"input": 300, "output": 40, "cost": {"total": 0.25}}, stop="toolUse"
                ),
                "",
                "not json at all",
            ]
        )
        + "\n"
    )
    summary = _summarize(stream)
    assert summary["steps"] == 2
    assert summary["tool_calls"] == 2
    assert summary["usage"] == {"input": 400, "output": 60, "cached": 5}
    assert summary["cost"] == 0.25
    # The last turn's reason is the run's reason.
    assert summary["stop_reason"] == "toolUse"
    assert not summary["error"]


def test_summary_survives_a_missing_pi_log(tmp_path: Path):
    """A pi that died before writing must still yield a record, not an exception."""
    summary = _summarize(tmp_path / "nope.txt")
    assert summary["steps"] == 0
    assert "unreadable" in summary["error"]


def test_sandbox_env_carries_the_key_under_its_canonical_name(agent, monkeypatch):
    """The host uses a run-specific var; the extension only reads the standard one.

    Getting this wrong is silent: the extension fails open, pi runs fine, and
    the run produces no trace at all.
    """
    monkeypatch.setattr(
        "scripts.harbor_agents.pi_agent.Laminar.serialize_span_context",
        staticmethod(lambda *a, **k: '{"trace_id": "abc"}'),
    )
    env = agent._sandbox_laminar_env()
    assert env["LMNR_PROJECT_API_KEY"] == "test-key"
    assert env["LMNR_SPAN_CONTEXT"] == '{"trace_id": "abc"}'


def test_sandbox_env_refuses_to_run_untraced(agent, monkeypatch):
    """No span context => the sandbox would open its own, unattributable trace."""
    monkeypatch.setattr(
        "scripts.harbor_agents.pi_agent.Laminar.serialize_span_context",
        staticmethod(lambda *a, **k: None),
    )
    with pytest.raises(RuntimeError, match="LMNR_SPAN_CONTEXT"):
        agent._sandbox_laminar_env()


def test_the_record_matches_the_bash_harness_apart_from_harness(agent, task_dir):
    """Both harnesses feed one dataset, so only `harness` may differ."""
    environment = _FakeEnvironment(task_dir)
    facts = agent._task_facts(environment)
    metadata = agent._trace_metadata(facts, {"passed": 0})

    assert metadata["harness"] == "pi"
    assert metadata["agent"] == "laminar-pi"
    assert metadata["runner"] == "harbor"
    assert metadata["source"] == "HealthAgentBench"
    # `model` is the model, not the route to it -- the bash harness names the
    # same deployment without a provider prefix, and the two must group together.
    assert metadata["model"] == "claude-sonnet-5"
    assert metadata["model_provider"] == "anthropic"
    # Same polarity as everywhere else: true means the answer is wrong.
    assert metadata["gt_event_identified"] is True
    assert metadata["passed"] is False
    assert facts["gold_source"] == "microsoft-hand-audit"


def test_submission_path_comes_from_the_task_not_a_constant(agent, task_dir):
    """Pi writes the answer itself, so we must read back the path the task names."""
    environment = _FakeEnvironment(task_dir)
    assert (
        agent._submission_path(environment)
        == "/workspace/submission/eligible_trials.txt"
    )


@pytest.mark.asyncio
async def test_a_failed_readback_records_no_verdict(agent, task_dir, laminar):
    """Pi's answer lives in the sandbox: unread is not the same as empty.

    `_finalize` runs from `run()`'s `finally`, so on a timeout the task is
    already cancelled and the `cat` gets cancelled with it. Scoring the "" we
    were left holding would stamp `gt_event_identified: true` -- "this agent
    answered wrong" -- on a run whose answer may be sitting on disk, and that
    label is the whole point of the trajectory.
    """
    environment = _FinalizeEnvironment(task_dir, read_error=asyncio.CancelledError())
    context = SimpleNamespace()
    agent._stop_reason = "timeout"

    await agent._finalize(environment, context, agent._task_facts(environment))

    assert laminar.metadata["submission_readback_failed"] is True
    assert "gt_event_identified" not in laminar.metadata
    assert "passed" not in laminar.metadata
    # Everything we *did* manage to read still lands.
    assert laminar.metadata["num_steps"] == 3
    assert laminar.metadata["num_tool_calls"] == 5
    assert laminar.metadata["stop_reason"] == "timeout"
    assert context.metadata["lmnr_trace_id"]


@pytest.mark.asyncio
async def test_an_empty_submission_we_did_read_is_still_a_verdict(agent, task_dir, laminar):
    """The other half of the distinction: pi ran, wrote nothing, and was wrong."""
    environment = _FinalizeEnvironment(task_dir, submission="")
    await agent._finalize(environment, SimpleNamespace(), agent._task_facts(environment))

    assert "submission_readback_failed" not in laminar.metadata
    assert laminar.metadata["passed"] is False
    assert laminar.metadata["gt_event_identified"] is True


@pytest.mark.asyncio
async def test_a_correct_submission_read_back_scores_as_correct(agent, task_dir, laminar):
    """And a real answer is scored by the task's own evaluator, host-side."""
    gold = (task_dir / "tests" / "gold.txt").read_text()
    environment = _FinalizeEnvironment(task_dir, submission=gold)
    await agent._finalize(environment, SimpleNamespace(), agent._task_facts(environment))

    assert laminar.metadata["passed"] is True
    assert laminar.metadata["gt_event_identified"] is False
    assert laminar.metadata["recall_top_50"] == 1.0


@pytest.mark.asyncio
async def test_a_scoring_failure_does_not_escape_finalize(agent, task_dir, laminar, monkeypatch):
    """`_finalize` runs in a `finally`; raising there would swallow the real error."""
    def boom(*args, **kwargs):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(agent, "_score", boom)
    environment = _FinalizeEnvironment(task_dir, submission="NCT00304863\n")
    await agent._finalize(environment, SimpleNamespace(), agent._task_facts(environment))

    assert "evaluator exploded" in laminar.metadata["scoring_failed"]
    assert "gt_event_identified" not in laminar.metadata


@pytest.mark.asyncio
async def test_a_custom_provider_is_written_where_pi_reads_it(tmp_path):
    """The whole point of `pi_models`: reach a model pi's registry doesn't have."""
    agent = _pi_agent(
        tmp_path, model_name="azure-foundry/gpt-5.6-terra", pi_models=PI_MODELS
    )
    commands: list[str] = []

    async def fake_exec(environment, command, **kwargs):
        commands.append(command)

    agent.exec_as_agent = fake_exec
    await agent._write_pi_models(object())

    assert len(commands) == 1
    assert ".pi/agent/models.json" in commands[0]
    written = commands[0].split("<<'HAB_EOF'\n", 1)[1].rsplit("\nHAB_EOF", 1)[0]
    assert json.loads(written) == PI_MODELS
    # The key travels as a variable *name* for pi to resolve in the sandbox --
    # a config that pasted the key itself would put it in git.
    assert json.loads(written)["providers"]["azure-foundry"]["apiKey"].isupper()


def test_a_provider_pi_has_never_heard_of_fails_at_construction(tmp_path):
    """`--provider nope` fails inside the sandbox, which reads as a dead trial."""
    with pytest.raises(ValueError, match="does not define"):
        _pi_agent(tmp_path, model_name="openai/gpt-5.6-terra", pi_models=PI_MODELS)


def test_a_model_the_provider_block_does_not_define_fails_at_construction(tmp_path):
    with pytest.raises(ValueError, match="not model"):
        _pi_agent(
            tmp_path, model_name="azure-foundry/gpt-5.6-luna", pi_models=PI_MODELS
        )


def test_an_endpoint_only_override_keeps_the_builtin_model_list(tmp_path):
    """A provider block with no `models` re-points a built-in provider."""
    agent = _pi_agent(
        tmp_path,
        model_name="anthropic/claude-sonnet-5",
        pi_models={"providers": {"anthropic": {"baseUrl": "https://proxy.invalid"}}},
    )
    assert agent.pi_models["providers"]["anthropic"]["baseUrl"]


@pytest.mark.asyncio
async def test_the_pi_version_is_read_back_off_the_sandbox(tmp_path, task_dir):
    """Pi installs `@latest`, so the version is only knowable after the install."""
    agent = _pi_agent(tmp_path, model_name="azure-foundry/gpt-5.6-terra")

    async def fake_exec(environment, command, **kwargs):
        # `pi --version` prints to stderr; without the redirect the output is
        # empty, which is exactly what harbor's own detection sees.
        assert "pi --version 2>&1" in command
        return _ExecResult(stdout="0.73.1\n")

    agent.exec_as_agent = fake_exec
    await agent._detect_pi_version(object())

    metadata = agent._trace_metadata(agent._task_facts(_FakeEnvironment(task_dir)), {})
    assert metadata["harness_version"] == "0.73.1"


@pytest.mark.asyncio
async def test_a_version_left_on_stderr_is_still_read(tmp_path):
    """Where pi prints it is pi's business; a future release may move it back."""
    agent = _pi_agent(tmp_path, model_name="azure-foundry/gpt-5.6-terra")

    async def fake_exec(environment, command, **kwargs):
        return _ExecResult(stdout="", stderr="0.74.0\n")

    agent.exec_as_agent = fake_exec
    await agent._detect_pi_version(object())

    assert agent.version() == "0.74.0"


@pytest.mark.asyncio
async def test_a_version_we_cannot_read_costs_the_record_not_the_trial(tmp_path):
    """Best-effort: harbor's own detection already fails silently on daytona."""
    agent = _pi_agent(tmp_path, model_name="azure-foundry/gpt-5.6-terra")

    async def fake_exec(environment, command, **kwargs):
        raise RuntimeError("command failed")

    agent.exec_as_agent = fake_exec
    await agent._detect_pi_version(object())

    assert agent.version() is None


def test_the_agent_name_is_not_harbor_stock_pi(agent):
    """`-a pi` and this agent produce different records; they must not share a name."""
    assert agent.name() == "laminar-pi"
