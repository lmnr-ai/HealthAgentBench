#!/usr/bin/env python3
"""Emit Harbor task directories for TREC Clinical Trials topics.

Reproduces the upstream ``clinical_trial_matching_task_*`` layout, which was
reverse-engineered from the 9 committed tasks and holds exactly:

    pool  == all judged grade-0 + all judged grade-1 + gold
    gold  == the audited "clean eligible" subset of judged grade-2
    dropped == every grade-2 that was not confirmed by the audit

Committed per task: the topic ID, the shuffled ``trial_ncts.txt`` the bootstrap
downloads, the sorted ``pool_ncts.txt`` the verifier scores against, and
``gold.txt``. **Not** committed: the patient description and the raw qrels — the
bootstrap derives both at run time so we never redistribute TREC data.

Usage::

    # generate from an audit produced by audit_eligible.py
    uv run python scripts/trec_ct/build_tasks.py --year 2022 \
        --audit assets/clinical_trial_matching/audit/2022.jsonl --out tasks

    # regenerate the 9 committed 2021 tasks from their own gold.txt files
    # (round-trip check: the result must be identical to what is in git)
    uv run python scripts/trec_ct/build_tasks.py --year 2021 \
        --gold-from-existing tasks --out tasks
"""

from __future__ import annotations

import argparse
import json
import random
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trec_data as td

VERBATIM_ENV_FILES = ("Dockerfile", "fetch_trials.py", "extract_task_inputs.py")
VERBATIM_TEST_FILES = ("harbor_evaluator.py", "verify.py", "test.sh")


def task_name(year: td.Year, topic_id: int) -> str:
    """2021 keeps upstream's bare naming so existing task dirs round-trip."""
    if year.year == 2021:
        return f"clinical_trial_matching_task_{topic_id}"
    return f"clinical_trial_matching_{year.year}_task_{topic_id}"


def load_audit(path: Path, year: td.Year) -> dict[int, list[str]]:
    """Read an ``audit_eligible.py`` JSONL into ``{topic_id: [gold nct, ...]}``.

    Rows are keyed on ``(year, topic_id)``, not ``topic_id`` alone: topic
    numbers restart every track (2021 has 1-75, 2022 1-50, 2023 1-40), so an
    audit file from the wrong year would otherwise hand topic 12's gold from one
    patient to a completely different patient who also happens to be topic 12.
    """
    gold: dict[int, list[str]] = {}
    other_years: set[object] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("year") != year.year:
            other_years.add(row.get("year"))
            continue
        if row.get("clean_eligible"):
            gold.setdefault(row["topic_id"], []).append(row["nct_id"])
    if other_years:
        print(
            f"[build] {path}: ignored rows for year(s) {sorted(map(str, other_years))} "
            f"— building {year.year} only",
            file=sys.stderr,
        )
    return {k: sorted(set(v)) for k, v in gold.items()}


def load_existing_gold(tasks_dir: Path, year: td.Year) -> dict[int, list[str]]:
    """Read gold back out of already-generated task dirs (round-trip check)."""
    gold: dict[int, list[str]] = {}
    for task_dir in sorted(tasks_dir.glob("clinical_trial_matching*")):
        gold_file = task_dir / "tests" / "gold.txt"
        topic_file = task_dir / "environment" / "workspace" / "topic_id.txt"
        if not (gold_file.is_file() and topic_file.is_file()):
            continue
        topic_id = int(topic_file.read_text().strip())
        if task_dir.name != task_name(year, topic_id):
            continue
        gold[topic_id] = sorted(
            {line.strip() for line in gold_file.read_text().splitlines() if line.strip()}
        )
    return gold


def write_task(
    out_dir: Path,
    year: td.Year,
    topic_id: int,
    gold: list[str],
    pool: list[str],
    *,
    seed: int,
) -> None:
    name = task_name(year, topic_id)
    task_dir = out_dir / name
    env_dir = task_dir / "environment"
    tests_dir = task_dir / "tests"
    workspace = env_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    for fname in VERBATIM_ENV_FILES:
        td.copy_template(fname, env_dir / fname)
    for fname in VERBATIM_TEST_FILES:
        td.copy_template(fname, tests_dir / fname)
    (tests_dir / "test.sh").chmod(0o755)

    zip_urls = " ".join(shlex.quote(u) for u in year.corpus.zip_urls)
    td.render_template(
        "bootstrap.sh.tmpl",
        env_dir / "bootstrap.sh",
        {
            "YEAR": str(year.year),
            "TOPICS_URL": year.topics_url,
            "QRELS_URL": year.qrels_url,
            "ZIP_URLS": zip_urls,
        },
    )
    (env_dir / "bootstrap.sh").chmod(0o755)

    cache_mount = (
        td.DEFAULT_CACHE_ROOT.relative_to(td.REPO_ROOT) / year.corpus.cache_dirname
    )
    td.render_template(
        "docker-compose.yaml.tmpl",
        env_dir / "docker-compose.yaml",
        {"CACHE_MOUNT": cache_mount.as_posix()},
    )
    td.render_template("task.toml.tmpl", task_dir / "task.toml", {"TASK_ID": name})
    td.render_template(
        "instruction.md.tmpl",
        task_dir / "instruction.md",
        {
            "PATIENT_DOC_PHRASE": year.patient_doc_phrase,
            "TOPIC_FILE_PHRASE": year.topic_file_phrase,
            "SOURCE_PHRASE": year.source_phrase,
        },
    )
    td.render_template(
        "README.md.tmpl",
        task_dir / "README.md",
        {
            "TASK_ID": name,
            "YEAR": str(year.year),
            "TOPIC_ID": str(topic_id),
            "PATIENT_DOC_PHRASE": year.patient_doc_phrase,
            "POOL_SIZE": str(len(pool)),
            "GOLD_SIZE": str(len(gold)),
        },
    )

    # topic_id.txt has no trailing newline upstream; keep that byte-for-byte.
    (workspace / "topic_id.txt").write_text(str(topic_id))
    # trial_ncts.txt is the bootstrap's download list — shuffled, so the on-disk
    # ordering of /workspace/data/trials leaks nothing about which NCTs are gold.
    shuffled = list(pool)
    random.Random(f"{seed}:{year.year}:{topic_id}").shuffle(shuffled)
    (workspace / "trial_ncts.txt").write_text("\n".join(shuffled) + "\n")
    (tests_dir / "pool_ncts.txt").write_text("\n".join(sorted(pool)) + "\n")
    (tests_dir / "gold.txt").write_text("\n".join(sorted(gold)) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, choices=sorted(td.YEARS))
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--audit", type=Path, help="JSONL from audit_eligible.py")
    src.add_argument(
        "--gold-from-existing",
        type=Path,
        help="Reuse gold.txt from already-generated task dirs under this path.",
    )
    parser.add_argument("--out", type=Path, default=Path("tasks"))
    parser.add_argument(
        "--topics", default="all", help="'all', '1-50', or '6,8,19'."
    )
    parser.add_argument("--max-pool", type=int, default=500)
    parser.add_argument("--min-gold", type=int, default=3)
    parser.add_argument("--max-gold", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--cache-root", type=Path, default=None)
    args = parser.parse_args(argv)

    year = td.get_year(args.year)
    qrels = td.load_qrels(year, args.cache_root)

    if args.audit:
        gold_by_topic = load_audit(args.audit, year)
    else:
        gold_by_topic = load_existing_gold(args.gold_from_existing, year)
    if not gold_by_topic:
        raise SystemExit(
            f"[build] no {year.year} gold found in the requested source "
            "(wrong --year for this audit file?)"
        )

    # Belt and braces on the year check above: gold must be grade-2 in *this*
    # year's qrels. A gold NCT that isn't tells us the source and --year
    # disagree, and would otherwise be unioned into the pool as a trial the
    # verifier demands but TREC never judged eligible for this patient.
    for topic_id, gold in sorted(gold_by_topic.items()):
        judged = qrels.get(topic_id, {})
        stray = [n for n in gold if judged.get(n) != td.GRADE_ELIGIBLE]
        if stray:
            raise SystemExit(
                f"[build] topic {topic_id}: {len(stray)} gold trial(s) are not "
                f"grade-2 in qrels{year.year} (e.g. {stray[:3]}) — the gold "
                "source does not match --year"
            )

    from audit_eligible import parse_topic_selector

    selected = parse_topic_selector(args.topics, sorted(gold_by_topic))
    written, skipped = 0, []
    for topic_id in selected:
        gold = gold_by_topic[topic_id]
        if len(gold) < args.min_gold:
            skipped.append((topic_id, f"only {len(gold)} clean-eligible"))
            continue
        if len(gold) > args.max_gold:
            # Deterministically trim; the trimmed ones stay out of the pool, so
            # they can never become hidden positives.
            gold = sorted(
                random.Random(f"{args.seed}:gold:{topic_id}").sample(
                    gold, args.max_gold
                )
            )
        pool = td.build_pool(
            qrels[topic_id], gold, max_pool=args.max_pool, seed=args.seed + topic_id
        )
        write_task(args.out, year, topic_id, gold, pool, seed=args.seed)
        written += 1
        print(
            f"[build] {task_name(year, topic_id)}: pool={len(pool)} gold={len(gold)}"
        )

    for topic_id, why in skipped:
        print(f"[build] skipped topic {topic_id}: {why}", file=sys.stderr)
    print(f"[build] wrote {written} task(s) under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
