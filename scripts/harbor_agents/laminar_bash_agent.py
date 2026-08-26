"""One of the trajectory harnesses: a bash tool-loop we drive ourselves.

Harbor calls ``BaseAgent.run()`` in-process on the *host* and hands it an
``environment`` handle that proxies into the task sandbox. So the whole
tool-calling loop lives here, on the host, and every LLM call and every tool
call is something we issue ourselves -- which is what makes the trace shape
below ours to choose. ``pi_agent.py`` is the other end of that spectrum: a
real coding agent running inside the sandbox, instrumented from within.

Everything about the *record* a run leaves behind -- metadata schema, host-side
scoring, the answer-key probe -- is shared and lives in ``trajectory.py``. Only
the loop is here.

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

Usage -- prefer the job config, which pins every knob in one reviewable file::

    HAB_LMNR_PROJECT_API_KEY=... uv run harbor run -c configs/laminar-bash.yaml

Everything it sets is also reachable from the CLI::

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
import json
import logging
import re
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from lmnr import Laminar
from openai import AsyncOpenAI

from .trajectory import TrajectoryAgent

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

def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... [{len(text) - limit} characters truncated] ...\n\n{tail}"


class LaminarBashAgent(TrajectoryAgent, BaseAgent):
    """A bash-tool loop over the Chat Completions API, traced to Laminar."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    # The loop in this file is what shaped the trajectory, so that is what
    # `harness` names. See TrajectoryAgent.HARNESS.
    HARNESS = "custom/laminar-bash-loop"

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
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            logger=logger,
            lmnr_key_var=lmnr_key_var,
            **kwargs,
        )
        self.max_steps = int(max_steps)
        self.exec_timeout_sec = int(exec_timeout_sec)
        self.base_url = base_url or self._env("OPENAI_BASE_URL")
        self.api_key = self._env(api_key_var) or self._env("OPENAI_API_KEY")
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

    @staticmethod
    def name() -> str:
        return "laminar-bash"

    def version(self) -> str | None:
        return importlib.metadata.version("openai")

    async def setup(self, environment: BaseEnvironment) -> None:
        """Nothing to install: the loop runs on the host, not in the sandbox."""
        return

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
        self._init_laminar()
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
                # Never None here: this loop writes the submission itself, so
                # "" is a real answer (the model never submitted one), not an
                # unknown. Pi is the harness that can lose the answer.
                self._score_and_record(environment, context, facts, submission_text)
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
