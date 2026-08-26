"""The Pi harness: a real coding agent, traced from inside the sandbox.

``laminar_bash_agent.py`` runs its loop on the host, so it can wrap every step
in a span itself. Pi is the opposite: it runs *in the sandbox* as a CLI, and the
only honest way to trace it is from within its own process. That is what
`@lmnr-ai/pi-extension <https://github.com/lmnr-ai/lmnr-pi-extension>`_ does --
it subscribes to Pi's lifecycle events and emits LLM/tool spans live, in
process, through the Laminar TS SDK.

So this agent is thin. It installs the extension next to Pi, hands the sandbox
a project key and -- the load-bearing part -- ``LMNR_SPAN_CONTEXT``, which the
TS SDK reads on ``Laminar.initialize`` and adopts as its parent. Without it the
extension would open its own root trace inside the sandbox, and the host would
have no id to stamp metadata on or to write into ``result.json``: the run's
trajectories would be unattributable. With it, the two processes share one
trace::

    <task_id>                      root, DEFAULT   (host, this file)
      └─ pi agent run              DEFAULT         (sandbox, extension)
          ├─ LLM call (turn 0)     LLM
          ├─ bash                  TOOL
          └─ ...

One level deeper than the bash harness, and deliberately so -- the nesting is
what says "these steps happened inside Pi". ``verify_traces.py`` knows the
expected depth per harness.

Everything else -- scoring, metadata, the answer-key probe -- is the shared
machinery in ``trajectory.py``, so a pi trajectory and a bash trajectory are
the same record with a different ``harness``.

Usage::

    HAB_LMNR_PROJECT_API_KEY=... ANTHROPIC_API_KEY=... \
        uv run harbor run -c configs/pi.yaml

``model_name`` must be ``<provider>/<model>`` -- that is Pi's own requirement,
not ours.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from lmnr import Laminar

from .trajectory import TrajectoryAgent

#: The extension that makes a Pi run show up in Laminar at all.
DEFAULT_EXTENSION = "npm:@lmnr-ai/pi-extension"

#: Aggregates Pi's JSON event stream *in the sandbox*. Pi's ``--mode json``
#: output carries every message in full -- megabytes on a long run -- and it is
#: needed while the host root span is still open, so it cannot wait for Harbor
#: to sync ``/logs``. Reducing it in place keeps one small JSON object crossing
#: the sandbox boundary. Kept as source text (rather than a file we would have
#: to upload) so ``tests/test_pi_agent.py`` can run it against synthetic
#: streams with the host's own interpreter.
PI_SUMMARY_SCRIPT = r"""
import json, sys

steps = 0
tool_calls = 0
usage = {"input": 0, "output": 0, "cached": 0}
cost = 0.0
stop_reason = ""
error = ""
try:
    with open(sys.argv[1]) as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "message_end":
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            steps += 1
            u = message.get("usage") or {}
            usage["input"] += u.get("input") or 0
            usage["output"] += u.get("output") or 0
            usage["cached"] += u.get("cacheRead") or 0
            cost += (u.get("cost") or {}).get("total") or 0.0
            content = message.get("content")
            if isinstance(content, list):
                tool_calls += sum(
                    1
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "toolCall"
                )
            stop_reason = message.get("stopReason") or stop_reason
            error = message.get("errorMessage") or error
except OSError as exc:
    error = "pi output unreadable: %r" % (exc,)

print(json.dumps({
    "steps": steps,
    "tool_calls": tool_calls,
    "usage": usage,
    "cost": cost,
    "stop_reason": stop_reason,
    "error": error,
}))
"""


class LaminarPiAgent(TrajectoryAgent, Pi):
    """Harbor's Pi agent, wrapped so its run lands in our trajectory schema."""

    HARNESS = "pi"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        pi_extension: str = DEFAULT_EXTENSION,
        **kwargs,
    ):
        super().__init__(
            logs_dir=logs_dir, model_name=model_name, logger=logger, **kwargs
        )
        self.pi_extension = pi_extension

    @staticmethod
    def name() -> str:
        # Not `Pi.name()`: that returns the registered harbor agent name "pi",
        # and returning it here would let a stock `-a pi` run and this one look
        # identical in `agent` metadata while producing different records.
        return "laminar-pi"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Install Pi (upstream), then the Laminar extension next to it."""
        await super().setup(environment)
        if not self.pi_extension:
            return
        await self.exec_as_agent(
            environment,
            command=(
                f". ~/.nvm/nvm.sh; pi install {shlex.quote(self.pi_extension)}"
            ),
        )
        self.logger.info("installed pi extension %s", self.pi_extension)

    def _sandbox_laminar_env(self) -> dict[str, str]:
        """What the extension needs, resolved on the host.

        The key deliberately does *not* travel under its own name on the host:
        ``lmnr_key_var`` points at a run-specific variable so a batch can't
        inherit an ambient ``LMNR_PROJECT_API_KEY`` and land in the wrong
        project. Inside the sandbox the extension only reads the canonical
        name, so the rename happens here.
        """
        env = {
            "LMNR_PROJECT_API_KEY": self._env(self.lmnr_key_var) or "",
            # Continue the host's trace instead of opening a second one.
            "LMNR_SPAN_CONTEXT": Laminar.serialize_span_context() or "",
        }
        if self.lmnr_base_url:
            env["LMNR_BASE_URL"] = self.lmnr_base_url
        missing = [k for k, v in env.items() if not v]
        if missing:
            raise RuntimeError(
                f"cannot trace this Pi run: {missing} resolved empty. "
                "Refusing to record an unattributable trajectory."
            )
        return env

    async def _pi_summary(self, environment: BaseEnvironment) -> dict[str, Any]:
        """Reduce Pi's JSON event stream to counters, in the sandbox."""
        log_path = f"/logs/agent/{self._OUTPUT_FILENAME}"
        command = f"python3 -c {shlex.quote(PI_SUMMARY_SCRIPT)} {log_path}"
        try:
            result = await environment.exec(command, timeout_sec=120)
            return json.loads((result.stdout or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001 - never lose a trajectory over counters
            self.logger.warning("could not summarize pi output: %r", exc)
            return {}

    async def _read_submission(self, environment: BaseEnvironment) -> str:
        """Read back what Pi wrote, so we score the bytes the verifier will."""
        path = self._submission_path(environment)
        result = await environment.exec(f"cat {shlex.quote(path)} 2>/dev/null", timeout_sec=60)
        return result.stdout or ""

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._init_laminar()
        await self._assert_answer_key_absent(environment)
        facts = self._task_facts(environment)

        with Laminar.start_as_current_span(
            name=facts.get("task_id", "trajectory"),
            input=instruction,
            span_type="DEFAULT",
        ):
            # _extra_env is merged into every exec_as_agent call, which is how
            # these reach the `pi` process itself.
            self._extra_env.update(self._sandbox_laminar_env())
            try:
                await super().run(instruction, environment, context)
                self._stop_reason = "pi_exited"
            except asyncio.CancelledError:
                # Harbor's agent timeout cancels run(). Close the span with what
                # we have rather than losing the whole trajectory.
                self._stop_reason = "timeout"
                raise
            except Exception as exc:
                self._stop_reason = f"error: {type(exc).__name__}"
                self.logger.exception("pi run failed")
                raise
            finally:
                await self._finalize(environment, context, facts)
        Laminar.flush()

    async def _finalize(
        self,
        environment: BaseEnvironment,
        context: AgentContext,
        facts: dict[str, Any],
    ) -> None:
        """Collect the outcome and stamp the record. Never raises.

        Runs from ``run()``'s ``finally``, which on a timeout means the task is
        already cancelled -- so an ``exec`` here can be cancelled too. Losing
        the counters is survivable; losing the metadata means an unlabelled
        trajectory, so this swallows whatever the sandbox does and records what
        it has.
        """
        submission_text, summary = "", {}
        try:
            submission_text = await self._read_submission(environment)
            summary = await self._pi_summary(environment)
        except BaseException as exc:  # noqa: BLE001 - see docstring
            self.logger.warning("could not read back the pi run: %r", exc)

        self._n_llm_calls = summary.get("steps", 0)
        self._n_tool_calls = summary.get("tool_calls", 0)
        self._usage.update(summary.get("usage") or {})
        if summary.get("stop_reason") and self._stop_reason == "pi_exited":
            self._stop_reason = f"pi:{summary['stop_reason']}"
        self._submission = [
            line.strip() for line in submission_text.splitlines() if line.strip()
        ]

        extra: dict[str, Any] = {}
        if summary.get("cost"):
            extra["cost_usd"] = summary["cost"]
        if summary.get("error"):
            extra["agent_error"] = summary["error"]
        self._record(context, facts, self._score(environment, submission_text), extra)
