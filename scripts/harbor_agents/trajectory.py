"""What every trajectory harness in this repo shares.

A "harness" here is the agent scaffold whose behaviour a trajectory reflects --
our own bash loop (``laminar_bash_agent.py``), Pi (``pi_agent.py``), and
whatever comes next. They differ entirely in how they take a step; they must not
differ at all in the record they leave behind, because the trajectories are
consumed as one dataset. So the record lives here:

- ``init_laminar``           -- one initialization per process, keyed off a
  run-specific env var rather than the ambient ``LMNR_PROJECT_API_KEY``.
- ``TrajectoryAgent._task_facts``  -- the datapoint's provenance, read host-side
  off the task dir. Never enters a prompt.
- ``TrajectoryAgent._score``       -- the task's *own* ``harbor_evaluator.py``,
  applied host-side, so our verdict cannot drift from Harbor's reward.
- ``TrajectoryAgent._trace_metadata`` -- the trajectory-store schema.
- ``TrajectoryAgent._assert_answer_key_absent`` -- refuse to record a trajectory
  the model could have cheated on.

A harness subclasses ``TrajectoryAgent`` alongside its Harbor base class and
sets ``HARNESS``. Everything above it is then identical across harnesses by
construction, which is the point: ``harness`` is the only metadata key that is
supposed to differ.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, ClassVar

from harbor.environments.base import BaseEnvironment
from lmnr import Laminar
from lmnr.opentelemetry_lib.tracing.instruments import Instruments

# --- trace metadata constants (the schema the trajectory store expects) ------
SOURCE = "HealthAgentBench"
DOMAIN = "healthcare"
# Harbor only provisions the container and computes the reward; swapping it for
# another runner would not change a single step of the trajectory, so it belongs
# in a flat `runner` key rather than in `harness`.
RUNNER = "harbor"
UPSTREAM_REPO = "https://github.com/microsoft/HealthAgentBench"
FORK_REPO = "https://github.com/lmnr-ai/HealthAgentBench"
EVAL_CRITERION = "recall_top_50 == 1.0"
# What `gt_event_identified` means in the metadata, spelled out because the
# polarity is the opposite of what the name suggests to most readers: the
# trajectories train a model to find errors, so the "event" being identified is
# a mistake. true => the answer missed EVAL_CRITERION. See `_trace_metadata`.
GT_EVENT = "answer is wrong (missed the eval criterion)"

DEFAULT_SUBMISSION_PATH = "/workspace/submission/eligible_trials.txt"

_GOLD_SOURCE_RE = re.compile(r'^\s*gold_source\s*=\s*"([^"]*)"', re.MULTILINE)
_SUBMISSION_PATH_RE = re.compile(r'^\s*submission_path\s*=\s*"([^"]*)"', re.MULTILINE)

_init_lock = threading.Lock()
_initialized = False


def init_laminar(
    logger: logging.Logger,
    key_var: str,
    base_url: str | None = None,
    project_api_key: str | None = None,
) -> None:
    """Initialize Laminar once per process.

    Harbor runs trials concurrently in one event loop, so several agents share
    this process and race here on the first trial.

    ``key_var`` names the environment variable holding the project key. It is a
    parameter rather than a hard-coded ``LMNR_PROJECT_API_KEY`` because that
    name is generic enough to already be set by whatever shell you are in --
    and a run that inherits somebody else's key writes a whole batch of
    trajectories into the wrong project, silently and unrecoverably.

    ``project_api_key`` short-circuits the lookup, for callers that resolve the
    var themselves (a job config's ``agents[].env`` never reaches ``os.environ``).
    """
    global _initialized
    with _init_lock:
        if _initialized:
            return
        project_api_key = project_api_key or os.environ.get(key_var)
        if not project_api_key:
            raise ValueError(f"No Laminar project key: set ${key_var}")
        Laminar.initialize(
            project_api_key=project_api_key,
            base_url=base_url,
            # Only OpenAI. Everything else would trace harbor's own plumbing
            # (daytona_sdk, threading, ...) into the trajectory. Harnesses that
            # call the model inside the sandbox instrument themselves there and
            # are unaffected by this.
            instruments={Instruments.OPENAI},
            # Do NOT claim the global tracer provider. The Daytona SDK
            # self-instruments via `trace.get_tracer()` on the *global*
            # provider (daytona/_utils/otel_decorator.py), so claiming it
            # exports a stray root trace per SDK call — `AsyncFileSystem.
            # search_files`, `Sandbox.process_execute_command`, and so on.
            # Laminar's own instrumentors resolve through its TracerWrapper,
            # not the global provider, so OpenAI spans are unaffected.
            set_global_tracer_provider=False,
        )
        _initialized = True
        # Fingerprint, not the key: enough to tell two projects apart in a log
        # after the fact, which is the only way to catch a misrouted batch.
        logger.info(
            "Laminar initialized from $%s (key %s..., openai instrumentation only)",
            key_var,
            project_api_key[:6],
        )


class TrajectoryAgent:
    """Mixin: the half of a trajectory harness that must not vary.

    Mixed in *before* the Harbor base class, so ``__init__`` can claim the
    Laminar kwargs and hand everything else on::

        class LaminarPiAgent(TrajectoryAgent, Pi):
            HARNESS = "pi"
    """

    #: The agent scaffold whose behaviour this trajectory reflects. Consumers
    #: group and compare trajectories by this, so it names the loop that took
    #: the steps -- never the runner that started the sandbox.
    HARNESS: ClassVar[str] = "unknown"

    logs_dir: Path
    logger: logging.Logger
    model_name: str | None

    def __init__(
        self,
        *args,
        lmnr_key_var: str = "LMNR_PROJECT_API_KEY",
        lmnr_base_url: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ):
        self.lmnr_key_var = lmnr_key_var
        self.lmnr_base_url = lmnr_base_url or os.environ.get("LMNR_BASE_URL")
        # Harbor hands `agents[].env` from the job config down as `extra_env`.
        # Keep it: it is how a config file supplies credentials to a harness
        # that runs on the host and therefore never sees the sandbox's env.
        self._extra_env: dict[str, str] = dict(extra_env or {})
        super().__init__(*args, extra_env=extra_env, **kwargs)

        # Filled in during run(), read by _trace_metadata().
        self._n_llm_calls = 0
        self._n_tool_calls = 0
        self._usage = {"input": 0, "output": 0, "cached": 0}
        self._submission: list[str] | None = None
        self._stop_reason = "unknown"

    def _env(self, key: str, default: str | None = None) -> str | None:
        """Job-config ``env`` first, then the ambient environment."""
        return self._extra_env.get(key) or os.environ.get(key) or default

    def _init_laminar(self) -> None:
        init_laminar(
            self.logger,
            self.lmnr_key_var,
            self.lmnr_base_url,
            self._env(self.lmnr_key_var),
        )

    # -- task facts -------------------------------------------------------
    def _task_dir(self, environment: BaseEnvironment) -> Path:
        return Path(environment.environment_dir).parent

    def _submission_path(self, environment: BaseEnvironment) -> str:
        """Where the task expects the answer, per its own ``task.toml``."""
        toml = self._task_dir(environment) / "task.toml"
        match = _SUBMISSION_PATH_RE.search(toml.read_text()) if toml.is_file() else None
        return match.group(1) if match else DEFAULT_SUBMISSION_PATH

    def _task_facts(self, environment: BaseEnvironment) -> dict[str, Any]:
        """Read the datapoint's provenance off the host-side task directory.

        Everything here is host-only. None of it is ever put in a prompt --
        ``tests/`` holds the answer key and is not mounted into the container
        the agent talks to.
        """
        task_dir = self._task_dir(environment)
        facts: dict[str, Any] = {"task_id": task_dir.name}

        parts = task_dir.name.split("_")
        # 2021 keeps upstream's bare naming; later years carry the year.
        year = (
            int(parts[3])
            if len(parts) > 3 and parts[3].isdigit() and len(parts[3]) == 4
            else 2021
        )
        facts["year"] = year
        facts["dataset"] = f"TREC Clinical Trials {year}"
        facts["dataset_url"] = f"https://www.trec-cds.org/{year}.html"

        topic_file = task_dir / "environment" / "workspace" / "topic_id.txt"
        if topic_file.is_file():
            facts["topic_id"] = int(topic_file.read_text().strip())

        toml = task_dir / "task.toml"
        if toml.is_file():
            match = _GOLD_SOURCE_RE.search(toml.read_text())
            if match:
                facts["gold_source"] = match.group(1)

        for key, rel in (("n_gold", "tests/gold.txt"), ("n_pool", "tests/pool_ncts.txt")):
            path = task_dir / rel
            if path.is_file():
                facts[key] = len([x for x in path.read_text().splitlines() if x.strip()])
        return facts

    def _score(self, environment: BaseEnvironment, submission_text: str) -> dict[str, Any]:
        """Apply the task's own verifier to the submission, host-side.

        We import ``tests/harbor_evaluator.py`` from the task rather than
        reimplementing the criterion, so ``gt_event_identified`` cannot drift
        from the reward Harbor's verifier computes in the container.
        """
        task_dir = self._task_dir(environment)
        evaluator_path = task_dir / "tests" / "harbor_evaluator.py"
        gold = task_dir / "tests" / "gold.txt"
        pool = task_dir / "tests" / "pool_ncts.txt"
        if not (evaluator_path.is_file() and gold.is_file()):
            return {}

        spec = importlib.util.spec_from_file_location(
            f"_hab_eval_{task_dir.name}", evaluator_path
        )
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        scratch = Path(self.logs_dir) / "self_eval"
        scratch.mkdir(parents=True, exist_ok=True)
        submission_file = scratch / "submission.txt"
        submission_file.write_text(submission_text)
        module.evaluate(submission_file, gold, pool, scratch)
        return json.loads((scratch / "metrics.json").read_text())

    def _trace_metadata(self, facts: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
        """The trajectory-store schema, with our own metadata flattened in."""
        metadata: dict[str, Any] = {
            "source": SOURCE,
            "domain": DOMAIN,
            "generated": True,
            "harness": self.HARNESS,
            "model": self.model_name or "unknown",
            "num_steps": self._n_llm_calls,
            # -- general metadata, flattened into the top level --
            "agent": self.name(),
            "harness_version": self.version() or "unknown",
            "runner": RUNNER,
            "runner_version": importlib.metadata.version("harbor"),
            "benchmark_repo": FORK_REPO,
            "upstream_repo": UPSTREAM_REPO,
            "eval_criterion": EVAL_CRITERION,
            "gt_event": GT_EVENT,
            "num_tool_calls": self._n_tool_calls,
            "n_input_tokens": self._usage["input"],
            "n_output_tokens": self._usage["output"],
            "stop_reason": self._stop_reason,
            **facts,
        }
        if metrics:
            # `gt_event_identified` marks the *error*, not the success: these
            # trajectories train a model to spot mistakes and inefficiencies, so
            # true means "there is something wrong here" -- i.e. the answer
            # missed the criterion. Absent metrics => no verdict, and the schema
            # says null rather than a defaulted False.
            metadata["gt_event_identified"] = not metrics.get("passed")
            metadata["passed"] = bool(metrics.get("passed"))
            for key in (
                "recall",
                "recall_top_20",
                "recall_top_50",
                "precision",
                "f1",
                "n_predicted",
                "n_true_positives",
                "n_false_negatives",
                "n_discarded_outside_pool",
            ):
                if key in metrics:
                    metadata[key] = metrics[key]
        return metadata

    async def _assert_answer_key_absent(self, environment: BaseEnvironment) -> None:
        """Abort if the agent's container can reach the answer key.

        ``tests/`` holds gold.txt and the runtime-derived qrels.txt, and the
        task's compose file mounts it into the *bootstrap* service only. That
        is load-bearing but it is a property of a file we generate, checked by
        nothing at run time -- so a bad edit to docker-compose.yaml, or a
        backend that flattens per-service mounts, would silently produce
        trajectories where the model could read the answers. Cheap enough
        (one exec) to run on every trial rather than trusting the invariant.
        """
        probe = "ls /tests/gold.txt /tests/qrels.txt /tests/pool_ncts.txt 2>/dev/null"
        result = await environment.exec(probe, timeout_sec=30)
        leaked = [line for line in (result.stdout or "").splitlines() if line.strip()]
        if leaked:
            raise RuntimeError(
                f"answer key reachable from the agent container: {leaked}. "
                "Refusing to record this trajectory."
            )
        # Logged so a trial's log shows the check ran, not just that it was quiet.
        self.logger.info("answer-key probe clean")

    # -- the record -------------------------------------------------------
    def _record(
        self,
        context: Any,
        facts: dict[str, Any],
        metrics: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Close out the trajectory: span output, trace metadata, trial metadata.

        Must be called with the harness's root span still current -- trace
        metadata is stamped through the active span, and ``lmnr_trace_id`` is
        read off it. That id is what ties a row in ``jobs/<name>/*/result.json``
        to a trace, and it is the only thing that separates one run's traces
        from another's inside the same Laminar project.
        """
        metadata = {**self._trace_metadata(facts, metrics), **(extra or {})}
        Laminar.set_span_output(
            {
                "submission": self._submission or [],
                "stop_reason": self._stop_reason,
                "passed": metadata.get("passed"),
            }
        )
        Laminar.set_trace_metadata(metadata)

        context.n_input_tokens = self._usage["input"]
        context.n_output_tokens = self._usage["output"]
        context.n_cache_tokens = self._usage["cached"]
        context.metadata = {
            **metadata,
            "lmnr_trace_id": str(Laminar.get_trace_id()),
        }
        self.logger.info(
            "trajectory %s: harness=%s steps=%s tools=%s passed=%s trace=%s",
            facts.get("task_id"),
            self.HARNESS,
            self._n_llm_calls,
            self._n_tool_calls,
            metadata.get("passed"),
            context.metadata["lmnr_trace_id"],
        )
        return metadata
