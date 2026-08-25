# Gold provenance

Every `clinical_trial_matching*` task scores the agent against a `gold.txt` — the
trials the patient is actually eligible for. This directory records where each
task's gold came from.

## The two sources

| `gold_source` | Tasks | Who decided |
| --- | --- | --- |
| `microsoft-hand-audit` | 9 | Upstream HealthAgentBench's human audit |
| `llm-audit:claude-sonnet-5` | 101 | `scripts/trec_ct/audit_eligible.py` |

The marker lives in each task's `task.toml` under `[metadata]`, so it travels
into Harbor's trial results and you can slice runs by it after the fact:

```bash
# which source is this task?
grep gold_source tasks/clinical_trial_matching_task_19/task.toml

# every human-audited task
grep -l 'gold_source = "microsoft-hand-audit"' tasks/*/task.toml

# same thing from the index
jq -r 'select(.gold_source == "microsoft-hand-audit") | .task_id' provenance/tasks.jsonl
```

The 9 human-audited ones are 2021 topics **6, 8, 19, 26, 27, 29, 35, 45, 75**.
Note they are *not* distinguishable by task name — `clinical_trial_matching_task_19`
(upstream) and `clinical_trial_matching_task_20` (ours) look identical. Use the
marker, not the name. 2022 tasks are all ours and do carry the year in the name.

## What both sources have in common

Neither audit invents gold. **A trial can only become gold if TREC's own human
assessors already graded it `2` = eligible for that patient.** The audit is a
second, stricter pass *over that human-labelled set*, and it can only ever
remove trials, never add them. It exists because TREC's assessors graded topical
relevance rather than strict criterion-by-criterion eligibility, so grade-2 is
noisy — there are 60-300 of them per topic.

Anything the audit doesn't confirm is dropped from the candidate pool entirely,
rather than being left in as a non-gold trial. That is what keeps
`recall@top50 == 1.0` fair: a trial the agent could reasonably call eligible is
never sitting in the pool waiting to be scored as a miss.

So the difference between the two sources is *who ran the second filter*, not
whether there is a real answer key. Both are grounded in the same human TREC
judgments.

There is no model anywhere in the scoring path: `tests/harbor_evaluator.py` is
set arithmetic over NCT IDs.

## Files

| File | Contents |
| --- | --- |
| `tasks.jsonl` | One row per committed task: `task_id`, `year`, `topic_id`, `gold_source`, `n_gold`, `n_pool`. |
| `audit_2021.jsonl` | One row per audited candidate: `year`, `topic_id`, `nct_id`, `model`, `clean_eligible`, `votes[{verdict, confidence}]`. |
| `audit_2022.jsonl` | Same, for 2022. |

These are metadata only. The auditor's raw output also carries a free-text
`reason` per verdict that paraphrases the patient description; that stays in
`assets/clinical_trial_matching/audit/` (gitignored) because we don't
redistribute TREC topics or anything derived from them.

Regenerate with:

```bash
uv run python scripts/trec_ct/export_provenance.py --years 2021 2022
```

Candidate sampling is seeded on `(seed, year, topic_id)`, so re-running
`audit_eligible.py` with the same `--seed` audits exactly the same trials and
the verdicts can be diffed against these files directly.

## Reading the audit files

```bash
# why is NCT01234567 not gold for 2021 topic 12?
jq 'select(.topic_id == 12 and .nct_id == "NCT01234567")' provenance/audit_2021.jsonl

# verdict spread for a year
jq -r '.votes[].verdict' provenance/audit_2021.jsonl | sort | uniq -c
```

A candidate is promoted only when **every** vote says `eligible` at or above
`--min-confidence` (0.7 for these runs). `borderline` and `not_eligible` both
mean "dropped from the pool", not "distractor".

Verdict spread across the 3,317 recorded candidates:

| Verdict | Count |
| --- | --- |
| `not_eligible` | 1,694 |
| `eligible` | 821 |
| `borderline` | 776 |
| `parse_error` | 26 |

729 of the 821 `eligible` verdicts became gold; the other 92 came in under the
0.7 confidence floor. The 26 `parse_error` rows are responses the JSON extractor
couldn't read — 0.8% of the run, and they fail closed: the candidate is dropped
from the pool rather than guessed at, so they cost coverage and can't corrupt an
answer key.
