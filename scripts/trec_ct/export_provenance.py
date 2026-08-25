#!/usr/bin/env python3
"""Export the committed provenance record for the generated tasks.

``audit_eligible.py`` writes its raw output under
``assets/clinical_trial_matching/audit/`` (gitignored), and those rows carry a
free-text ``reason`` per verdict that paraphrases the TREC patient description.
We don't redistribute TREC data, so the raw file stays local.

What *is* committed is the metadata: which trial was audited, under which
patient, by which model, and what it decided. That is enough to see why any
given trial is or isn't gold, and enough to re-run the audit and diff, without
shipping a derivative of the topics.

Writes:

- ``provenance/audit_<year>.jsonl`` — one row per audited candidate:
  ``{year, topic_id, nct_id, model, clean_eligible, votes: [{verdict, confidence}]}``
- ``provenance/tasks.jsonl``        — one row per committed task, carrying the
  ``metadata.gold_source`` marker that says who decided its gold.

Usage::

    uv run python scripts/trec_ct/export_provenance.py --years 2021 2022
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_tasks
import trec_data as td

VOTE_KEYS = ("verdict", "confidence")


def strip_row(row: dict) -> dict:
    """Keep the decision, drop the prose that quotes the patient."""
    return {
        "year": row.get("year"),
        "topic_id": row["topic_id"],
        "nct_id": row["nct_id"],
        "model": row.get("model"),
        "clean_eligible": bool(row.get("clean_eligible")),
        "votes": [
            {k: v.get(k) for k in VOTE_KEYS} for v in row.get("votes", [])
        ],
    }


def export_audit(year: int, audit_path: Path, out_path: Path) -> int:
    rows = [
        strip_row(json.loads(line))
        for line in audit_path.read_text().splitlines()
        if line.strip()
    ]
    rows = [r for r in rows if r["year"] == year]
    rows.sort(key=lambda r: (r["topic_id"], r["nct_id"]))
    out_path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    return len(rows)


def export_tasks(tasks_dir: Path, out_path: Path) -> int:
    rows = []
    for task_dir in sorted(tasks_dir.glob("clinical_trial_matching*")):
        gold_file = task_dir / "tests" / "gold.txt"
        topic_file = task_dir / "environment" / "workspace" / "topic_id.txt"
        if not (gold_file.is_file() and topic_file.is_file()):
            continue
        parts = task_dir.name.split("_")
        year = int(parts[3]) if parts[3].isdigit() and len(parts[3]) == 4 else 2021
        rows.append(
            {
                "task_id": task_dir.name,
                "year": year,
                "topic_id": int(topic_file.read_text().strip()),
                "gold_source": build_tasks.existing_gold_source(task_dir),
                "n_gold": len([x for x in gold_file.read_text().splitlines() if x.strip()]),
                "n_pool": len(
                    [
                        x
                        for x in (task_dir / "tests" / "pool_ncts.txt")
                        .read_text()
                        .splitlines()
                        if x.strip()
                    ]
                ),
            }
        )
    rows.sort(key=lambda r: (r["year"], r["topic_id"]))
    out_path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2021, 2022])
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=td.DEFAULT_CACHE_ROOT.parent / "audit",
        help="Where audit_eligible.py wrote its raw JSONL.",
    )
    parser.add_argument("--tasks", type=Path, default=Path("tasks"))
    parser.add_argument("--out", type=Path, default=Path("provenance"))
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for year in args.years:
        audit_path = args.audit_dir / f"{year}.jsonl"
        if not audit_path.is_file():
            print(f"[provenance] no audit for {year} at {audit_path}", file=sys.stderr)
            continue
        n = export_audit(year, audit_path, args.out / f"audit_{year}.jsonl")
        print(f"[provenance] audit_{year}.jsonl: {n} verdicts")

    n = export_tasks(args.tasks, args.out / "tasks.jsonl")
    print(f"[provenance] tasks.jsonl: {n} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
