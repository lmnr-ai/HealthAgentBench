"""A Harbor agent whose only job is to produce a clean Laminar trajectory.

Harbor calls ``BaseAgent.run()`` in-process on the *host* and hands it an
``environment`` handle that proxies into the task sandbox. So the whole
tool-calling loop lives here, on the host, and every LLM call and every tool
call is something we issue ourselves. That is the entire reason this agent
exists instead of ``-a codex`` or ``-a claude-code``: those shell out to a CLI
inside the sandbox, and whatever they emit is theirs to shape, not ours.

The trace comes out deliberately flat::

    <task_id>                    root, DEFAULT
      ├─ openai.chat             LLM   (auto-instrumented)
      ├─ bash                    TOOL
      ├─ openai.chat             LLM
      ├─ write_submission        TOOL
      └─ ...

Only three span kinds, two levels. Nothing wraps the loop, nothing wraps a
step, and only ``Instruments.OPENAI`` is enabled so that harbor's own use of
the Daytona SDK / threading doesn't inject spans of its own.

Trace metadata is stamped on the root span at the end of the run, once the
outcome is known -- see ``_trace_metadata``.

Usage::

    HAB_LMNR_PROJECT_API_KEY=... uv run harbor run \
        -p tasks -i clinical_trial_matching_task_19 \
        --agent-import-path scripts.harbor_agents.laminar_bash_agent:LaminarBashAgent \
        -m gpt-5.6-luna \
        --ak base_url=https://laminar-resource.services.ai.azure.com/openai/v1 \
        --ak api_key_var=AZURE_API_KEY \
        --ak lmnr_key_var=HAB_LMNR_PROJECT_API_KEY \
        --env daytona

Point ``lmnr_key_var`` at a run-specific variable. ``LMNR_PROJECT_API_KEY`` is
the default only for convenience; it is a common enough name that an ambient
one will happily send a whole batch to somebody else's project.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from lmnr import Laminar
from lmnr.opentelemetry_lib.tracing.instruments import Instruments
from openai import AsyncOpenAI

# --- trace metadata constants (the schema the trajectory store expects) ------
SOURCE = "HealthAgentBench"
DOMAIN = "healthcare"
HARNESS = "harbor"
UPSTREAM_REPO = "https://github.com/microsoft/HealthAgentBench"
FORK_REPO = "https://github.com/lmnr-ai/HealthAgentBench"
EVAL_CRITERION = "recall_top_50 == 1.0"
# What `gt_event_identified` means in the metadata, spelled out because the
# polarity is the opposite of what the name suggests to most readers: the
# trajectories train a model to find errors, so the "event" being identified is
# a mistake. true => the answer missed EVAL_CRITERION. See `_trace_metadata`.
GT_EVENT = "answer is wrong (missed the eval criterion)"

# Tool output that goes back into the prompt. Trial XMLs run to tens of KB and
# a pool holds hundreds of them, so an untruncated `cat` would blow the context
# window in a single step.
MAX_TOOL_OUTPUT_CHARS = 16_000
# Once the transcript passes this, the oldest tool results get replaced by a
# stub. Trial documents are re-readable at any time, so dropping an old dump is
# recoverable for the model in a way that dropping its own reasoning is not.
MAX_TRANSCRIPT_CHARS = 400_000
COMPACTED_STUB = "[output dropped to save context -- re-run the command if you still need it]"

_NCT_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)

SYSTEM_PROMPT = f"""\
You are an autonomous agent working inside a Linux sandbox. You solve the task \
by calling tools; you cannot see the machine except through them.

Guidance:
- Start by orienting yourself: read the patient note, then see how many \
candidate trials there are.
- The trial XMLs are large. Prefer targeted extraction (grep/sed/python3) over \
dumping whole files, and batch work into loops instead of one command per file.
- python3 is available in the sandbox. For anything involving more than a \
couple of files, write a script.
- Tool output is truncated at {MAX_TOOL_OUTPUT_CHARS} characters, so keep each \
command's output small and focused.
- Work through the whole candidate pool. Do not stop after the first few \
promising trials.
- When you are done, call write_submission with your final answer. A successful \
call ends the run, so make exactly one -- but if it comes back reporting an \
error, nothing was recorded: fix the problem and call it again.\
"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a bash command in the task sandbox and return its combined "
                "stdout/stderr. State persists on disk between calls, but each "
                "call is a fresh shell (no cd/export carry-over)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to run."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_submission",
            "description": (
                "Write the final answer, when you have finished reviewing the "
                "candidate pool. A successful call ends the run, so make exactly "
                "one. If the call reports an error the answer was not recorded "
                "and the run is still going: correct it and call again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nct_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The NCT identifiers the patient is eligible for, in "
                            "descending order of confidence."
                        ),
                    }
                },
                "required": ["nct_ids"],
            },
        },
    },
]

_init_lock = threading.Lock()
_initialized = False


def _init_laminar(logger: logging.Logger, key_var: str) -> None:
    """Initialize Laminar once per process.

    Harbor runs trials concurrently in one event loop, so several agents share
    this process and race here on the first trial.

    ``key_var`` names the environment variable holding the project key. It is a
    parameter rather than a hard-coded ``LMNR_PROJECT_API_KEY`` because that
    name is generic enough to already be set by whatever shell you are in --
    and a run that inherits somebody else's key writes a whole batch of
    trajectories into the wrong project, silently and unrecoverably.
    """
    global _initialized
    with _init_lock:
        if _initialized:
            return
        project_api_key = os.environ.get(key_var)
        if not project_api_key:
            raise ValueError(f"No Laminar project key: set ${key_var}")
        Laminar.initialize(
            project_api_key=project_api_key,
            # Only OpenAI. Everything else would trace harbor's own plumbing
            # (daytona_sdk, threading, ...) into the trajectory.
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


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... [{len(text) - limit} characters truncated] ...\n\n{tail}"


class LaminarBashAgent(BaseAgent):
    """A bash-tool loop over the Chat Completions API, traced to Laminar."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        max_steps: int = 120,
        base_url: str | None = None,
        api_key_var: str = "OPENAI_API_KEY",
        lmnr_key_var: str = "LMNR_PROJECT_API_KEY",
        exec_timeout_sec: int = 300,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, logger=logger, **kwargs)
        self.max_steps = int(max_steps)
        self.lmnr_key_var = lmnr_key_var
        self.exec_timeout_sec = int(exec_timeout_sec)
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.api_key = os.environ.get(api_key_var) or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(f"No API key: set ${api_key_var} or $OPENAI_API_KEY")

        headers = None
        if self.base_url and "azure" in self.base_url:
            # Azure's OpenAI-compatible v1 surface authenticates on `api-key`;
            # the SDK only sends `Authorization: Bearer`.
            headers = {"api-key": self.api_key}
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=headers,
            max_retries=5,
        )

        # Filled in during run(), read by _trace_metadata().
        self._n_llm_calls = 0
        self._n_tool_calls = 0
        self._usage = {"input": 0, "output": 0, "cached": 0}
        self._submission: list[str] | None = None
        self._stop_reason = "unknown"

    @staticmethod
    def name() -> str:
        return "laminar-bash"

    def version(self) -> str | None:
        return importlib.metadata.version("openai")

    async def setup(self, environment: BaseEnvironment) -> None:
        """Nothing to install: the loop runs on the host, not in the sandbox."""
        return

    # -- task facts -------------------------------------------------------
    def _task_dir(self, environment: BaseEnvironment) -> Path:
        return Path(environment.environment_dir).parent

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
        year = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() and len(parts[3]) == 4 else 2021
        facts["year"] = year
        facts["dataset"] = f"TREC Clinical Trials {year}"
        facts["dataset_url"] = f"https://www.trec-cds.org/{year}.html"

        topic_file = task_dir / "environment" / "workspace" / "topic_id.txt"
        if topic_file.is_file():
            facts["topic_id"] = int(topic_file.read_text().strip())

        toml = task_dir / "task.toml"
        if toml.is_file():
            match = re.search(r'^\s*gold_source\s*=\s*"([^"]*)"', toml.read_text(), re.MULTILINE)
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
            "harness": HARNESS,
            "model": self.model_name or "unknown",
            "num_steps": self._n_llm_calls,
            # -- general metadata, flattened into the top level --
            "agent": self.name(),
            "harness_version": importlib.metadata.version("harbor"),
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

    # -- tools ------------------------------------------------------------
    async def _run_bash(self, environment: BaseEnvironment, command: str) -> str:
        result = await environment.exec(
            command, cwd="/workspace", timeout_sec=self.exec_timeout_sec
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        if result.return_code != 0:
            parts.append(f"[exit code {result.return_code}]")
        return _truncate("\n".join(parts).strip() or "[no output]")

    async def _write_submission(
        self, environment: BaseEnvironment, nct_ids: Any
    ) -> tuple[str, str]:
        """Write the submission into the sandbox.

        Returns ``(tool_output, text_on_disk)``. The second element is empty
        unless the file really landed, because it is what the run ends on and
        what we score host-side -- and a host-side verdict computed from a
        submission the container never got would disagree with Harbor's reward,
        which is the one thing this agent must never do.
        """
        # A model that emits `nct_ids` as one string instead of an array is a
        # normal tool-calling slip; iterating it would walk characters and throw
        # a perfectly good answer away.
        raw_ids = _NCT_RE.findall(nct_ids) if isinstance(nct_ids, str) else list(nct_ids or [])

        clean: list[str] = []
        seen: set[str] = set()
        for raw in raw_ids:
            match = _NCT_RE.search(str(raw))
            if match and match.group(0).upper() not in seen:
                seen.add(match.group(0).upper())
                clean.append(match.group(0).upper())
        if not clean:
            # Don't end the run on an empty answer -- hand the model the problem.
            return (
                (
                    "[no NCT identifiers found in nct_ids. Pass an array of IDs "
                    'like ["NCT01234567", ...]. The run has not ended; try again.]'
                ),
                "",
            )
        text = "\n".join(clean) + "\n"

        target = "/workspace/submission/eligible_trials.txt"
        # Heredoc rather than echo: no quoting hazards, and the payload is
        # newline-separated NCT IDs so the delimiter can never collide.
        script = (
            f"mkdir -p $(dirname {target}) && cat > {target} <<'HAB_EOF'\n{text}HAB_EOF\n"
            f"wc -l < {target}"
        )
        result = await environment.exec(script, timeout_sec=60)
        if result.return_code != 0:
            return (
                (
                    f"[failed to write submission: {result.stderr}] The run has "
                    "not ended; fix the problem and call write_submission again."
                ),
                "",
            )
        return f"Wrote {len(clean)} NCT IDs to {target}.", text

    # -- the loop ---------------------------------------------------------
    def _compact(self, messages: list[dict[str, Any]]) -> None:
        """Drop the oldest tool outputs when the transcript gets too long."""
        total = sum(len(str(m.get("content") or "")) for m in messages)
        for message in messages:
            if total <= MAX_TRANSCRIPT_CHARS:
                return
            if message.get("role") == "tool" and message.get("content") != COMPACTED_STUB:
                total -= len(str(message["content"])) - len(COMPACTED_STUB)
                message["content"] = COMPACTED_STUB

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        _init_laminar(self.logger, self.lmnr_key_var)
        await self._assert_answer_key_absent(environment)
        facts = self._task_facts(environment)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        submission_text = ""

        with Laminar.start_as_current_span(
            name=facts.get("task_id", "trajectory"),
            input=instruction,
            span_type="DEFAULT",
        ):
            try:
                submission_text = await self._loop(messages, environment)
            except asyncio.CancelledError:
                # Harbor's agent timeout cancels run(). Let the span close with
                # what we have rather than losing the whole trajectory.
                self._stop_reason = "timeout"
                raise
            except Exception as exc:
                self._stop_reason = f"error: {type(exc).__name__}"
                self.logger.exception("agent loop failed")
                raise
            finally:
                metrics = self._score(environment, submission_text)
                metadata = self._trace_metadata(facts, metrics)
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
                    "trajectory %s: steps=%s tools=%s passed=%s trace=%s",
                    facts.get("task_id"),
                    self._n_llm_calls,
                    self._n_tool_calls,
                    metadata.get("passed"),
                    context.metadata["lmnr_trace_id"],
                )
        Laminar.flush()

    async def _loop(self, messages: list[dict[str, Any]], environment: BaseEnvironment) -> str:
        """Tool-calling loop. Returns the submission text (empty if never written)."""
        for _ in range(self.max_steps):
            self._compact(messages)
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=TOOLS,
                max_completion_tokens=32_000,
            )
            self._n_llm_calls += 1
            if response.usage:
                self._usage["input"] += response.usage.prompt_tokens or 0
                self._usage["output"] += response.usage.completion_tokens or 0
                details = response.usage.prompt_tokens_details
                self._usage["cached"] += getattr(details, "cached_tokens", 0) or 0

            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                # No tool call and no submission: nudge once rather than
                # silently ending a trajectory with no answer.
                if self._submission is None:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You have not called write_submission yet. Either "
                                "keep working with bash or call write_submission now."
                            ),
                        }
                    )
                    continue
                self._stop_reason = "model_stopped"
                return ""

            for tool_call in message.tool_calls:
                fn = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                with Laminar.start_as_current_span(
                    name=fn, input=arguments, span_type="TOOL"
                ):
                    self._n_tool_calls += 1
                    if fn == "bash":
                        output = await self._run_bash(environment, arguments.get("command", ""))
                        submission_text = None
                    elif fn == "write_submission":
                        ids = arguments.get("nct_ids") or []
                        output, submission_text = await self._write_submission(environment, ids)
                        # Only on a write that landed: leaving `_submission` None
                        # keeps the "you never submitted" nudge armed.
                        if submission_text:
                            self._submission = [x for x in submission_text.splitlines() if x]
                    else:
                        output, submission_text = f"[unknown tool: {fn}]", None
                    Laminar.set_span_output(output)

                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": output}
                )
                if fn == "write_submission" and submission_text:
                    self._stop_reason = "submitted"
                    return submission_text

        self._stop_reason = "max_steps"
        return ""
