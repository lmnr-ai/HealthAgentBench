#!/usr/bin/env python3
"""Collect a run's Laminar trace ids into one manifest.

A Laminar project is not a run. Two batches pointed at the same project
interleave, and nothing in the trace metadata tells them apart -- same
``source``, same ``harness``, same ``model``. What *is* unique per trial is the
trace id the agent recorded in ``result.json``, so that list is the only
reliable way to say "these are the trajectories from this run".

Pass every job directory the run produced, including the retry passes that
picked up trials lost to sandbox flake::

    uv run python scripts/harbor_agents/trace_manifest.py \
        jobs/full1 jobs/full1retry jobs/full1retry2 -o jobs/full1/trace_manifest.json

Trials that died in environment setup never reached the agent, so they have no
trace and are reported as gaps rather than skipped silently.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIELDS = (
    "task_id",
    "passed",
    "gt_event_identified",
    "recall_top_50",
    "num_steps",
    "num_tool_calls",
    "model",
    "harness",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dirs", nargs="+", type=Path, help="jobs/<name> directories.")
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)

    entries: dict[str, dict] = {}
    gaps: dict[str, str] = {}
    for job_dir in args.job_dirs:
        for path in sorted(job_dir.glob("*/result.json")):
            result = json.loads(path.read_text())
            metadata = (result.get("agent_result") or {}).get("metadata") or {}
            name = result.get("task_name", path.parent.name)
            if not metadata:
                # Environment setup failed. Keep it only until some later job
                # dir supplies a real trajectory for the same task.
                exception = result.get("exception_info") or {}
                gaps.setdefault(name, str(exception.get("exception_type")))
                continue
            gaps.pop(name, None)
            entries[name] = {
                "trace_id": metadata.get("lmnr_trace_id"),
                "job": job_dir.name,
                **{k: metadata.get(k) for k in FIELDS},
            }

    manifest = [entries[k] for k in sorted(entries)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=1) + "\n")

    passed = sum(1 for e in manifest if e["passed"])
    print(f"{len(manifest)} trajectories -> {args.out}")
    print(f"  passed {passed}, gt_event_identified {len(manifest) - passed}")
    if gaps:
        print(f"  {len(gaps)} task(s) never produced a trajectory:")
        for name, kind in sorted(gaps.items()):
            print(f"    {name} ({kind})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
