#!/usr/bin/env python3
"""Emit Harbor task directories for TREC Clinical Trials topics.

Reproduces the upstream ``clinical_trial_matching_task_*`` layout, which was
reverse-engineered from the 9 committed tasks and holds exactly:

    pool  == all judged grade-0 + all judged grade-1 + gold
    gold  == the audited "clean eligible" subset of judged grade-2
    dropped == every grade-2 that was not confirmed by the audit

Task dirs are **generated, not committed** — every one of them holds the same
eleven files rendered from ``templates/`` (six byte-identical across all 110),
and the only per-task bytes are derived from that task's gold. Harbor requires a
task dir to be self-contained, so there is nothing to share and no include
mechanism to reach for; the framework's answer is this adapter, which commits
the input instead. What is committed is ``provenance/tasks.jsonl`` (the index:
gold + ``gold_source`` + the hashes of the generated answer files) plus this
directory. Everything under ``tasks/`` is reproducible from those and is
gitignored. **Not** committed anywhere: the patient description and the raw
qrels — the bootstrap derives both at run time so we never redistribute TREC
data.

Usage::

    # the normal path: rebuild every task from the committed index
    uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl

    # generate from a fresh audit produced by audit_eligible.py
    uv run python scripts/trec_ct/build_tasks.py --year 2022 \
        --audit assets/clinical_trial_matching/audit/2022.jsonl --out tasks

    # reuse gold from task dirs already on disk (e.g. after editing templates/)
    uv run python scripts/trec_ct/build_tasks.py --year 2021 \
        --gold-from-existing tasks --out tasks
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trec_data as td

VERBATIM_ENV_FILES = ("Dockerfile", "fetch_trials.py", "extract_task_inputs.py")
VERBATIM_TEST_FILES = ("harbor_evaluator.py", "verify.py", "test.sh")

# What a task's ``metadata.gold_source`` says when it predates the marker. Only
# upstream's original 9 tasks were ever in that state.
UPSTREAM_GOLD_SOURCE = "microsoft-hand-audit"
_GOLD_SOURCE_RE = re.compile(r'^\s*gold_source\s*=\s*"([^"]*)"', re.MULTILINE)


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


def audit_gold_source(path: Path, year: td.Year) -> str:
    """``llm-audit:<model>`` for the models that produced this year's verdicts."""
    models = {
        row.get("model")
        for line in path.read_text().splitlines()
        if line.strip()
        for row in [json.loads(line)]
        if row.get("year") == year.year and row.get("model")
    }
    return "llm-audit:" + ("+".join(sorted(models)) if models else "unknown")


def existing_gold_source(task_dir: Path) -> str:
    """Read ``metadata.gold_source`` back out of a generated task.

    Tasks written before the marker existed are upstream's original 9, so that
    is what a missing marker means — never a silent "unknown".
    """
    toml = task_dir / "task.toml"
    match = _GOLD_SOURCE_RE.search(toml.read_text()) if toml.is_file() else None
    return match.group(1) if match else UPSTREAM_GOLD_SOURCE


def load_index(path: Path) -> list[dict]:
    """Read ``provenance/tasks.jsonl`` — the committed dataset index."""
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def index_gold(rows: list[dict], year: td.Year) -> tuple[dict[int, list[str]], dict[int, str]]:
    """Split the index into ``({topic: gold}, {topic: gold_source})`` for one year.

    Like ``load_audit``, this is keyed on ``(year, topic_id)``: topic numbers
    restart every track, so a row from another year would hand one patient's
    gold to a different patient with the same topic number.
    """
    gold: dict[int, list[str]] = {}
    source: dict[int, str] = {}
    for row in rows:
        if row.get("year") != year.year:
            continue
        topic_id = int(row["topic_id"])
        gold[topic_id] = sorted(set(row["gold"]))
        source[topic_id] = row.get("gold_source") or UPSTREAM_GOLD_SOURCE
    return gold, source


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
    gold_source: str,
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
    td.render_template(
        "task.toml.tmpl",
        task_dir / "task.toml",
        {"TASK_ID": name, "GOLD_SOURCE": gold_source},
    )
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
            "GOLD_SOURCE": gold_source,
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


def build_year(year: td.Year, args: argparse.Namespace) -> int:
    """Write every selected task for one track year. Returns the count."""
    qrels = td.load_qrels(year, args.cache_root)

    if args.index:
        gold_by_topic, source_of = index_gold(load_index(args.index), year)
    elif args.audit:
        gold_by_topic = load_audit(args.audit, year)
        source_of = dict.fromkeys(
            gold_by_topic, audit_gold_source(args.audit, year)
        )
    else:
        gold_by_topic = load_existing_gold(args.gold_from_existing, year)
        # Regenerating must never relabel a task: carry each one's existing
        # marker across, so upstream's 9 stay upstream's.
        source_of = {
            topic_id: existing_gold_source(
                args.gold_from_existing / task_name(year, topic_id)
            )
            for topic_id in gold_by_topic
        }
    if not gold_by_topic:
        raise SystemExit(
            f"[build] no {year.year} gold found in the requested source "
            "(wrong --year for this file?)"
        )

    # Belt and braces on the year check above: gold must be grade-2 in *this*
    # year's qrels. A gold NCT that isn't tells us the source and the year
    # disagree, and would otherwise be unioned into the pool as a trial the
    # verifier demands but TREC never judged eligible for this patient.
    for topic_id, gold in sorted(gold_by_topic.items()):
        judged = qrels.get(topic_id, {})
        stray = [n for n in gold if judged.get(n) != td.GRADE_ELIGIBLE]
        if stray:
            raise SystemExit(
                f"[build] topic {topic_id}: {len(stray)} gold trial(s) are not "
                f"grade-2 in qrels{year.year} (e.g. {stray[:3]}) — the gold "
                "source does not match this year"
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
            # they can never become hidden positives. A no-op on an index or an
            # existing tree, whose gold was already trimmed when it was built.
            gold = sorted(
                random.Random(f"{args.seed}:gold:{topic_id}").sample(
                    gold, args.max_gold
                )
            )
        pool = td.build_pool(
            qrels[topic_id], gold, max_pool=args.max_pool, seed=args.seed + topic_id
        )
        write_task(
            args.out,
            year,
            topic_id,
            gold,
            pool,
            seed=args.seed,
            gold_source=source_of[topic_id],
        )
        written += 1
        print(
            f"[build] {task_name(year, topic_id)}: pool={len(pool)} gold={len(gold)}"
        )

    for topic_id, why in skipped:
        print(f"[build] skipped topic {topic_id}: {why}", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        nargs="+",
        choices=sorted(td.YEARS),
        help="Track year(s) to build. Defaults to every year in --index.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--index",
        type=Path,
        help="The committed dataset index (provenance/tasks.jsonl).",
    )
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

    if args.year:
        years = sorted(set(args.year))
    elif args.index:
        # Building the whole dataset is the common case, and the index already
        # says which years it covers.
        years = sorted({row["year"] for row in load_index(args.index)})
    else:
        parser.error("--year is required unless --index is given")

    written = sum(build_year(td.get_year(y), args) for y in years)
    print(f"[build] wrote {written} task(s) under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
