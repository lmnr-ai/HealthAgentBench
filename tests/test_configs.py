"""The shipped job configs are the harness selector, so they get tested.

A typo in `import_path` or a `lmnr_key_var` that quietly falls back to the
ambient `LMNR_PROJECT_API_KEY` costs a whole batch -- 110 tasks, ~95 minutes,
and in the second case the trajectories land in someone else's project and
can't be moved. All of that is checkable without a sandbox, so it is.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml
from harbor.models.job.config import JobConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.harbor_agents.trajectory import TrajectoryAgent  # noqa: E402

CONFIGS = sorted((REPO_ROOT / "configs").glob("*.yaml"))
# The generic name an ambient shell is likely to have already exported.
GENERIC_KEY_VAR = "LMNR_PROJECT_API_KEY"


def _load(path: Path) -> JobConfig:
    return JobConfig.model_validate(yaml.safe_load(path.read_text()))


def test_there_is_a_config_per_harness():
    assert {p.stem for p in CONFIGS} == {"laminar-bash", "pi"}


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_config_parses_and_names_a_real_agent(path: Path):
    config = _load(path)
    assert config.agents, "a config with no agent runs harbor's oracle"

    for agent in config.agents:
        assert agent.import_path, "harness must be explicit, not harbor's default"
        module_path, class_name = agent.import_path.split(":", 1)
        agent_class = getattr(importlib.import_module(module_path), class_name)
        # Not just importable: it has to produce our record.
        assert issubclass(agent_class, TrajectoryAgent)
        assert agent_class.HARNESS != TrajectoryAgent.HARNESS
        assert agent.model_name


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_config_pins_a_run_specific_laminar_key_var(path: Path):
    """The default is convenience; a batch run must not rely on it."""
    for agent in _load(path).agents:
        key_var = agent.kwargs.get("lmnr_key_var")
        assert key_var and key_var != GENERIC_KEY_VAR
        # And the var it names has to actually be supplied to the agent.
        assert key_var in agent.env


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_config_holds_no_literal_secrets(path: Path):
    """`env` values are `${VAR}` templates -- never a pasted key."""
    for agent in _load(path).agents:
        for name, value in agent.env.items():
            assert value == f"${{{name}}}", f"{name} is not a plain env reference"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_config_points_at_the_generated_task_dir(path: Path):
    """Relative to the repo root, because that is where harbor is invoked."""
    config = _load(path)
    assert config.datasets
    for dataset in config.datasets:
        assert dataset.path == Path("tasks")


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_config_retries_the_setup_flake(path: Path):
    """~12% of trials die in environment setup; without this they need a manual pass."""
    config = _load(path)
    assert config.retry.max_retries > 0
    # Harbor's defaults must keep excluding real failures from the retry.
    assert "AgentTimeoutError" in (config.retry.exclude_exceptions or set())
