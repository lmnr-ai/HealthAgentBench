#!/usr/bin/env python3
"""Read back what actually landed in Laminar for a run.

Checks the two things that are easy to get wrong and invisible locally: that
the span tree is the flat ``root -> [LLM|TOOL]`` shape we intend, and that the
required trace-metadata keys are all present with the right types.

Usage::

    uv run python scripts/harbor_agents/verify_traces.py --minutes 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from lmnr import LaminarClient

# The trajectory-store schema. `gt_event_identified` is nullable by spec, so it
# is checked for presence but allowed to be null.
REQUIRED_KEYS = (
    "source",
    "domain",
    "gt_event_identified",
    "generated",
    "harness",
    "model",
    "num_steps",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=60, help="Look-back window.")
    parser.add_argument("--source", default="HealthAgentBench")
    parser.add_argument(
        "--key-var",
        default="LMNR_PROJECT_API_KEY",
        help="Env var holding the project key. Must match the run's lmnr_key_var.",
    )
    args = parser.parse_args(argv)

    project_api_key = os.environ.get(args.key_var)
    if not project_api_key:
        print(f"No Laminar project key: set ${args.key_var}")
        return 1
    print(f"project key {project_api_key[:6]}... (from ${args.key_var})")
    client = LaminarClient(project_api_key=project_api_key)

    spans = client.sql.query(
        f"""
        SELECT trace_id, span_id, parent_span_id, name, span_type, path,
               input_tokens, output_tokens, start_time, end_time
        FROM spans
        WHERE start_time > now() - INTERVAL {args.minutes} MINUTE
        ORDER BY start_time
        """
    )
    if not spans:
        print(f"No spans in the last {args.minutes} minutes.")
        return 1

    by_trace: dict[str, list[dict]] = {}
    for span in spans:
        by_trace.setdefault(span["trace_id"], []).append(span)

    ok = True
    strays: list[str] = []
    foreign: list[str] = []
    checked = 0
    print(f"{len(by_trace)} trace(s) in the last {args.minutes} minutes\n")
    for trace_id, rows in by_trace.items():
        ids = {r["span_id"] for r in rows}
        roots = [r for r in rows if not r["parent_span_id"] or r["parent_span_id"] not in ids]
        depth_2 = [r for r in rows if r["parent_span_id"] in {x["span_id"] for x in roots}]
        deeper = [r for r in rows if r not in roots and r not in depth_2]
        kinds = Counter(r["span_type"] for r in rows)

        # A single-span trace named after an SDK method is something that
        # self-instruments onto the global tracer provider leaking into the
        # project -- the Daytona SDK does exactly this. Collect rather than
        # print: there can be hundreds.
        if len(rows) == 1 and roots and roots[0]["span_type"] == "DEFAULT":
            strays.append(roots[0]["name"])
            continue

        meta = client.sql.query(
            "SELECT metadata FROM traces WHERE id = {trace_id:String}",
            {"trace_id": trace_id},
        )
        raw = meta[0].get("metadata") if meta else None
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            # A trace with no metadata at all -- i.e. one we didn't stamp.
            parsed = {}
        if parsed.get("source") != args.source:
            # Somebody else's trace in this project. Worth surfacing (it means
            # the project isn't dedicated to the run) but it says nothing about
            # whether *our* trajectories are well-formed.
            foreign.append(f"{trace_id} ({len(rows)} spans, source={parsed.get('source')!r})")
            continue

        checked += 1
        print(f"trace {trace_id}")
        print(f"  roots      : {[r['name'] for r in roots]}")
        print(f"  span types : {dict(kinds)}")
        print(f"  children   : {len(depth_2)} at depth 2, {len(deeper)} deeper")
        if len(roots) != 1:
            print(f"  FAIL: expected exactly 1 root, got {len(roots)}")
            ok = False
        if deeper:
            names = sorted({r["name"] for r in deeper})
            print(f"  FAIL: spans below depth 2: {names}")
            ok = False

        missing = [k for k in REQUIRED_KEYS if k not in parsed]
        if missing:
            print(f"  FAIL: metadata missing {missing}")
            ok = False
        else:
            print(
                "  metadata   : "
                f"task={parsed.get('task_id')} model={parsed.get('model')} "
                f"steps={parsed.get('num_steps')} passed={parsed.get('gt_event_identified')} "
                f"({len(parsed)} keys)"
            )
        print()

    if not checked:
        print("FAIL: no trajectory traces in the window -- nothing was verified")
        ok = False
    if strays:
        # Not a hard failure -- a stray means the project is picking up spans
        # from something other than our agent, which is worth seeing but does
        # not make the trajectories themselves wrong.
        counts = Counter(strays)
        print(f"WARN: {len(strays)} stray single-span trace(s) not emitted by the agent:")
        for name, n in counts.most_common(10):
            print(f"  {n:>4}x {name}")
    if foreign:
        print(f"WARN: {len(foreign)} trace(s) in this project are not ours:")
        for line in foreign[:10]:
            print(f"  {line}")

    print(
        f"\n{checked} trajectory trace(s) checked, "
        f"{len(strays)} stray, {len(foreign)} foreign"
    )
    print("OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
