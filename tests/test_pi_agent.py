"""Unit tests for the Pi harness's host-side half.

Pi itself runs in the sandbox and needs Docker, a model key and ~20 minutes, so
none of that is exercised here. What is exercised is everything that decides
whether the resulting trajectory is *usable*: the trace-continuation env the
extension needs, the counters we read back out of Pi's event stream, and the
fact that a pi record is the same record as a bash record with a different
``harness``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

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


@pytest.fixture
def agent(tmp_path) -> LaminarPiAgent:
    return LaminarPiAgent(
        logs_dir=tmp_path,
        model_name="anthropic/claude-sonnet-5",
        logger=logging.getLogger("test"),
        lmnr_key_var="HAB_LMNR_PROJECT_API_KEY",
        extra_env={"HAB_LMNR_PROJECT_API_KEY": "test-key"},
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
    assert metadata["model"] == "anthropic/claude-sonnet-5"
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


def test_the_agent_name_is_not_harbor_stock_pi(agent):
    """`-a pi` and this agent produce different records; they must not share a name."""
    assert agent.name() == "laminar-pi"
