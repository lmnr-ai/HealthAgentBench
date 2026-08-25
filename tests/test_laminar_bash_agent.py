"""Unit tests for the trajectory agent's host-side logic.

Everything here runs without a sandbox, a model, or a Laminar project: what is
being tested is the part of the agent whose bugs are silent in a real run --
the answer-key guard (which only speaks up when it fires), the provenance it
reads off the task dir, and the trace metadata it stamps. A wrong
``gold_source`` or a missing ``gt_event_identified`` produces a trajectory that
looks fine and is mislabelled forever.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.harbor_agents.laminar_bash_agent import LaminarBashAgent  # noqa: E402

# One of upstream's 9 hand-audited tasks, so the gold_source assertion below is
# about a marker we did not write ourselves.
TASK_DIR = REPO_ROOT / "tasks" / "clinical_trial_matching_task_6"

REQUIRED_KEYS = (
    "source",
    "domain",
    "generated",
    "harness",
    "model",
    "num_steps",
)


class _ExecResult:
    def __init__(self, stdout: str = "", stderr: str = "", return_code: int = 0):
        self.stdout, self.stderr, self.return_code = stdout, stderr, return_code


class _FakeEnvironment:
    """Records commands and replays a canned stdout for the probe."""

    def __init__(self, probe_stdout: str = ""):
        self.environment_dir = TASK_DIR / "environment"
        self.probe_stdout = probe_stdout
        self.commands: list[str] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append(command)
        return _ExecResult(stdout=self.probe_stdout)


@pytest.fixture
def agent(tmp_path, monkeypatch) -> LaminarBashAgent:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    return LaminarBashAgent(
        logs_dir=tmp_path,
        model_name="gpt-5.6-luna",
        logger=logging.getLogger("test"),
    )


@pytest.mark.asyncio
async def test_answer_key_guard_passes_when_tests_dir_is_not_mounted(agent):
    """The normal case: `ls` finds nothing, so the probe is quiet."""
    environment = _FakeEnvironment(probe_stdout="")
    await agent._assert_answer_key_absent(environment)
    assert environment.commands, "guard did not run any command"


@pytest.mark.asyncio
async def test_answer_key_guard_aborts_when_gold_is_reachable(agent):
    """A compose file that mounts tests/ into the agent service must fail loudly.

    This is the branch that never executes in a healthy run, which is exactly
    why it needs a test: a typo here would disable the guard permanently and
    nothing else would notice.
    """
    environment = _FakeEnvironment(probe_stdout="/tests/gold.txt\n/tests/qrels.txt\n")
    with pytest.raises(RuntimeError, match="answer key reachable"):
        await agent._assert_answer_key_absent(environment)


def test_task_facts_read_provenance_off_the_task_dir(agent):
    facts = agent._task_facts(_FakeEnvironment())
    assert facts["task_id"] == TASK_DIR.name
    assert facts["year"] == 2021
    assert facts["topic_id"] == 6
    assert facts["dataset"] == "TREC Clinical Trials 2021"
    assert facts["gold_source"] == "microsoft-hand-audit"
    assert facts["n_gold"] > 0
    assert facts["n_pool"] > facts["n_gold"]


def test_trace_metadata_has_every_required_key(agent):
    metadata = agent._trace_metadata(agent._task_facts(_FakeEnvironment()), {"passed": 1})
    missing = [k for k in REQUIRED_KEYS if k not in metadata]
    assert not missing, f"metadata missing {missing}"
    assert metadata["source"] == "HealthAgentBench"
    assert metadata["domain"] == "healthcare"
    assert metadata["generated"] is True
    assert metadata["gt_event_identified"] is True


def test_gt_event_identified_is_absent_rather_than_false_without_metrics(agent):
    """No verdict must read as null, not as a failed trajectory."""
    metadata = agent._trace_metadata(agent._task_facts(_FakeEnvironment()), {})
    assert "gt_event_identified" not in metadata


def test_score_uses_the_task_own_evaluator(agent, tmp_path):
    """`gt_event_identified` must come from the same code the verifier runs."""
    gold = [x for x in (TASK_DIR / "tests/gold.txt").read_text().splitlines() if x.strip()]
    metrics = agent._score(_FakeEnvironment(), "\n".join(gold) + "\n")
    assert metrics["passed"] == 1
    assert metrics["recall_top_50"] == 1.0
    assert metrics["n_gold_eligible"] == len(gold)

    metrics = agent._score(_FakeEnvironment(), "NCT00000000\n")
    assert metrics["passed"] == 0
