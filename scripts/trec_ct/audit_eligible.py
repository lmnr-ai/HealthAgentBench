#!/usr/bin/env python3
"""Audit TREC grade-2 trials down to a *clean-eligible* gold set.

Why this step exists
--------------------
TREC's own qrels already mark trials as ``2`` = "eligible", but there are 60-300
of them per topic and they are noisy — annotators graded relevance, not strict
criterion-by-criterion eligibility. Upstream HealthAgentBench therefore hand-
audited a handful per topic and used *only* those as gold, dropping every
un-audited grade-2 from the candidate pool so no hidden positives remain. Their
audit tooling was never published; this script reproduces it with an LLM.

What it does
------------
For each requested topic: sample N grade-2 candidates, fetch their trial XMLs,
and ask a model — once per ``--votes`` — whether the patient meets **every**
inclusion criterion and violates **no** exclusion criterion. A candidate is
promoted to gold only if every vote says ``eligible`` with confidence at or
above ``--min-confidence``. Everything else is recorded but not used.

Sampling is seeded by ``(year, topic_id)``, so reruns audit the same candidates
and the resulting tasks are reproducible.

Output: one JSON object per candidate, appended to ``--out`` (JSONL). Re-running
skips candidates already present in that file, so an interrupted audit resumes.

Usage::

    export ANTHROPIC_API_KEY=...
    uv run python scripts/trec_ct/audit_eligible.py \
        --year 2022 --topics 1-50 --sample-per-topic 24 \
        --out assets/clinical_trial_matching/audit/2022.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trec_data as td

DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You are a meticulous clinical trial eligibility auditor.

You are given one patient description and one clinical trial. Decide whether \
this specific patient could be enrolled in this specific trial.

Apply a strict standard:
- The patient must plausibly satisfy EVERY inclusion criterion.
- The patient must violate NO exclusion criterion.
- The trial must actually target this patient's condition/population.
- Missing information counts AGAINST eligibility only when the criterion is \
central to the trial (e.g. a required biomarker or prior therapy). Routine \
details a real screener would simply check (exact lab draw dates, consent, \
contraception agreements) do not by themselves make a patient ineligible.

Return one of three verdicts:
- "eligible"     — you would confidently put this patient forward for screening.
- "borderline"   — plausible but hinges on unstated information or a judgement call.
- "not_eligible" — an inclusion criterion is unmet or an exclusion criterion fires.

Respond with a single JSON object and nothing else:
{"verdict": "eligible"|"borderline"|"not_eligible",
 "confidence": <float 0-1>,
 "unmet_inclusion": [<short strings>],
 "violated_exclusion": [<short strings>],
 "reason": "<one or two sentences>"}
"""

USER_TEMPLATE = """\
## Patient

{patient}

## Candidate trial

{trial}

## Question

Is this patient eligible for this trial? Respond with the JSON object only.
"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message, file=sys.stderr, flush=True)


def parse_topic_selector(selector: str, known: list[int]) -> list[int]:
    """Parse ``all`` / ``1-50`` / ``6,8,19`` into a sorted topic-id list."""
    if selector.strip().lower() == "all":
        return sorted(known)
    wanted: set[int] = set()
    for chunk in selector.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(chunk))
    missing = wanted - set(known)
    if missing:
        _log(f"[audit] ignoring topics with no judgments: {sorted(missing)}")
    return sorted(wanted & set(known))


def _parse_verdict(text: str) -> dict:
    match = _JSON_BLOCK.search(text)
    if not match:
        return {"verdict": "parse_error", "confidence": 0.0, "reason": text[:400]}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"verdict": "parse_error", "confidence": 0.0, "reason": text[:400]}
    payload.setdefault("verdict", "parse_error")
    payload.setdefault("confidence", 0.0)
    return payload


def audit_one(client, model: str, patient: str, trial: str, votes: int) -> list[dict]:
    """Run ``votes`` independent eligibility judgements on one (patient, trial)."""
    out: list[dict] = []
    for _ in range(votes):
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": USER_TEMPLATE.format(patient=patient, trial=trial),
                }
            ],
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        out.append(_parse_verdict(text))
    return out


def is_clean_eligible(votes: list[dict], min_confidence: float) -> bool:
    return bool(votes) and all(
        v.get("verdict") == "eligible"
        and float(v.get("confidence") or 0.0) >= min_confidence
        for v in votes
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, choices=sorted(td.YEARS))
    parser.add_argument(
        "--topics",
        default="all",
        help="'all', a range like '1-50', or a list like '6,8,19'.",
    )
    parser.add_argument(
        "--sample-per-topic",
        type=int,
        default=24,
        help="How many grade-2 candidates to audit per topic. Upstream tasks "
        "ended up with 3-9 gold each; 24 candidates comfortably clears that.",
    )
    parser.add_argument(
        "--votes",
        type=int,
        default=1,
        help="Independent judgements per candidate; a candidate is promoted "
        "only if ALL votes say eligible at >= --min-confidence.",
    )
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Calibrated on 13 upstream gold + 18 other grade-2 trials across "
        "2021 topics 19/26/35 (see docs/TREC_CT_ENRICHMENT.md): "
        "claude-opus-5 at --min-confidence 0.7 reproduces 10/13 of upstream's "
        "hand-audited gold; claude-sonnet-5 reproduces 9/13 for ~5x less.",
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve topics, sample candidates and fetch XMLs, but make no "
        "model calls. Prints exactly what a real run would audit.",
    )
    args = parser.parse_args(argv)

    year = td.get_year(args.year)
    topics = td.load_topics(year, args.cache_root)
    qrels = td.load_qrels(year, args.cache_root)
    topic_ids = parse_topic_selector(args.topics, sorted(qrels))
    if not topic_ids:
        raise SystemExit("[audit] no topics selected")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[int, str]] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            done.add((row["topic_id"], row["nct_id"]))
        _log(f"[audit] resuming: {len(done)} candidates already audited")

    # Sample candidates first so the XML fetch is one batched pass over the
    # remote zips instead of one range-request storm per topic.
    plan: list[tuple[int, str]] = []
    for topic_id in topic_ids:
        eligible = sorted(
            nct
            for nct, grade in qrels[topic_id].items()
            if grade == td.GRADE_ELIGIBLE
        )
        rng = random.Random(f"{args.seed}:{args.year}:{topic_id}")
        sample = sorted(
            rng.sample(eligible, min(args.sample_per_topic, len(eligible)))
        )
        plan.extend(
            (topic_id, nct) for nct in sample if (topic_id, nct) not in done
        )

    if not plan:
        _log("[audit] nothing to do — every selected candidate is already audited")
        return 0

    _log(
        f"[audit] {len(topic_ids)} topics, {len(plan)} candidates to audit "
        f"({args.votes} vote(s) each) with {args.model}"
    )

    cache_dir = td.corpus_cache_dir(year.corpus, args.cache_root)
    xmls = td.fetch_trial_xmls({nct for _, nct in plan}, year.corpus, cache_dir)
    _log(f"[audit] trial XMLs available: {len(xmls)}/{len({n for _, n in plan})}")

    if args.dry_run:
        for topic_id, nct in plan[:20]:
            _log(f"[audit:dry-run] topic {topic_id} -> {nct} ({nct in xmls})")
        if len(plan) > 20:
            _log(f"[audit:dry-run] ... and {len(plan) - 20} more")
        return 0

    from anthropic import Anthropic  # imported lazily: --dry-run needs no key

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("[audit] ANTHROPIC_API_KEY is not set")
    client = Anthropic()

    write_lock = threading.Lock()
    out_handle = args.out.open("a", encoding="utf-8")
    counters = {"eligible": 0, "done": 0}

    def work(item: tuple[int, str]) -> None:
        topic_id, nct = item
        xml_path = xmls.get(nct)
        if xml_path is None:
            row = {
                "year": args.year,
                "topic_id": topic_id,
                "nct_id": nct,
                "clean_eligible": False,
                "votes": [{"verdict": "missing_xml", "confidence": 0.0}],
            }
        else:
            votes = audit_one(
                client,
                args.model,
                topics[topic_id],
                td.summarize_trial(xml_path),
                args.votes,
            )
            row = {
                "year": args.year,
                "topic_id": topic_id,
                "nct_id": nct,
                "model": args.model,
                "clean_eligible": is_clean_eligible(votes, args.min_confidence),
                "votes": votes,
            }
        with write_lock:
            out_handle.write(json.dumps(row) + "\n")
            out_handle.flush()
            counters["done"] += 1
            counters["eligible"] += int(row["clean_eligible"])
            if counters["done"] % 25 == 0:
                _log(
                    f"[audit] {counters['done']}/{len(plan)} audited, "
                    f"{counters['eligible']} clean-eligible so far"
                )

    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(work, plan))
    finally:
        out_handle.close()

    _log(
        f"[audit] done: {counters['done']} audited, "
        f"{counters['eligible']} clean-eligible -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
