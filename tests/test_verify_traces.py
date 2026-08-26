"""The span-shape check that tells two harnesses apart.

``verify_traces.py`` is the only thing that reads back what actually landed in
Laminar, and its depth rule used to be a constant ("nothing below depth 2").
That was right for the bash loop and wrong for every in-sandbox harness, so the
rule is now per-harness -- which makes it worth a test, because a depth check
that silently passes everything is indistinguishable from one that works.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "harbor_agents"))

import verify_traces  # noqa: E402


def _span(span_id: str, parent: str | None = None) -> dict:
    return {"span_id": span_id, "parent_span_id": parent, "name": span_id}


def test_bash_harness_is_two_deep_and_pi_is_three():
    """The two shapes we ship, as the checker sees them."""
    bash = [_span("root"), _span("llm", "root"), _span("bash", "root")]
    assert max(verify_traces.span_depths(bash).values()) == 2
    assert verify_traces.EXPECTED_MAX_DEPTH["custom/laminar-bash-loop"] == 2

    pi = [
        _span("root"),
        _span("pi agent run", "root"),
        _span("LLM call (turn 0)", "pi agent run"),
        _span("bash", "pi agent run"),
    ]
    assert max(verify_traces.span_depths(pi).values()) == 3
    assert verify_traces.EXPECTED_MAX_DEPTH["pi"] == 3


def test_a_span_whose_parent_is_outside_the_trace_is_a_root():
    """Only part of a trace lands in the query window; it must not count as deep."""
    rows = [_span("orphan", "somewhere-else"), _span("child", "orphan")]
    depths = verify_traces.span_depths(rows)
    assert depths["orphan"] == 1
    assert depths["child"] == 2


def test_a_cycle_does_not_hang_the_checker():
    """Diagnostics on a malformed tree must still produce output."""
    rows = [_span("a", "b"), _span("b", "a")]
    depths = verify_traces.span_depths(rows)
    assert set(depths) == {"a", "b"}
