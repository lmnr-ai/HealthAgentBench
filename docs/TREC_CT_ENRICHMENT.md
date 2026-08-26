# Growing the Clinical Trial Matching category

Findings from auditing what upstream HealthAgentBench actually ships for
`clinical_trial_matching`, and what it would take to extend it to the rest of
TREC Clinical Trials 2021 plus the 2022 and 2023 tracks.

Everything below was verified against the live TREC sources on 2026-08-25, not
inferred from the papers.

---

## TL;DR

| Question | Answer |
| --- | --- |
| Can we extend to the rest of TREC CT 2021? | **Yes.** 66 unused topics, same corpus, same plumbing. Only the gold-set audit has to be redone. |
| Can we add TREC CT 2022? | **Yes, and it's the cheapest win.** 50 topics, *identical* document collection, identical topic format. Literally zero new infrastructure. |
| Can we add TREC CT 2023? | **Yes, with two changes.** Different corpus snapshot (already wired up) and a different topic format (questionnaire, not admission note) that makes the task meaningfully different and the auditor much more conservative. |
| Is any of it gated? | **No.** Topics, qrels and all corpus snapshots are open HTTP downloads from `trec.nist.gov` / `www.trec-cds.org`. No login, no DUA, no rate limit hit during this investigation. |

Ceiling if we take all three years: **9 → up to ~150 tasks** (66 + 50 + 37 new
topics, minus topics where the audit can't find enough clean-eligible trials).

---

## 1. What upstream actually ships

The repo has 9 tasks, one per TREC CT 2021 topic: 6, 8, 19, 26, 27, 29, 35, 45,
75. Each task commits a topic ID, a candidate pool, and a gold set; the patient
note and the raw qrels are downloaded at run time by the bootstrap so the repo
never redistributes TREC data.

The paper calls the pool "a hand-audited subset". Their generator
(`generate_harbor_tasks.py`) is referenced in the code comments but was never
published. Comparing the 9 committed tasks against `qrels2021.txt` pins the
recipe exactly:

```
pool == every judged grade-0 trial
      + every judged grade-1 trial
      + gold

gold == a small hand-audited subset of the judged grade-2 trials
dropped == every other grade-2 trial
```

| Task | Topic | Judged | grade-2 total | gold | pool | grade-0 + grade-1 + gold == pool |
| --- | --- | --- | --- | --- | --- | --- |
| `..._6`  | 6  | 529 | 117 | 4 | 416 | ✅ |
| `..._8`  | 8  | 515 |  92 | 3 | 426 | ✅ |
| `..._19` | 19 | 412 | 115 | 4 | 301 | ✅ |
| `..._26` | 26 | 457 |  87 | 5 | 375 | ✅ |
| `..._27` | 27 | 502 |  57 | 6 | 451 | ✅ |
| `..._29` | 29 | 475 |  72 | 4 | 407 | ✅ |
| `..._35` | 35 | 441 |  82 | 4 | 363 | ✅ |
| `..._45` | 45 | 484 | 145 | 4 | 343 | ✅ |
| `..._75` | 75 | 554 | 141 | 9 | 422 | ✅ |

**Dropping the un-audited grade-2 trials is the load-bearing part.** It is what
guarantees the pool contains no hidden positive, which is what makes
`recall@top50 == 1.0` a fair pass criterion. Any generator that keeps them would
silently make every task unpassable.

`scripts/trec_ct/build_tasks.py` implements this recipe, and regenerating from
the committed index reproduces `pool_ncts.txt` and `gold.txt` **byte-for-byte**
for all 110 tasks — which is why `tasks/` doesn't need to be in git at all:

```bash
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl \
    --out /tmp/roundtrip
diff -r --brief /tmp/roundtrip tasks    # empty
```

`tests/test_trec_ct_roundtrip.py` locks that in as a regression test, against
the `gold_sha256` / `pool_sha256` recorded per task in the index.

So the *only* piece that isn't mechanical is choosing which grade-2 trials
become gold. Section 4 covers that.

---

## 2. What each track year gives us

| | 2021 | 2022 | 2023 |
| --- | --- | --- | --- |
| Topics published | 75 | 50 | 40 |
| Topics with judgments | 75 | 50 | **37** |
| Topic format | free-text admission note | free-text admission note | questionnaire (`field: value`) |
| qrels rows | 35,832 | 35,394 | 33,870 |
| grade 0 / 1 / 2 | 24,243 / 6,019 / 5,570 | 28,419 / 3,036 / 3,939 | 11,504 / 10,699 / 11,667 |
| Judged trials per topic (avg) | 478 | 708 | 915 |
| grade-2 per topic (median) | 62 | 60 | 273 |
| Corpus | `ClinicalTrials.2021-04-27` (5 zips, 375,580 trials) | **same** | `ClinicalTrials.2023-05-08` (6 zips, 451,538 trials) |
| Judged NCTs found in the 2021 corpus | 26,162 / 26,162 (100%) | 26,585 / 26,585 (100%) | 14,942 / 17,106 (87.3%) |
| Judged NCTs found in the 2023 corpus | — | — | 17,105 / 17,106 (100%) |
| Already used here | 9 topics | 0 | 0 |

Sources (all verified reachable, un-authenticated):

- `https://trec.nist.gov/data/trials/topics{2021,2022,2023}.xml`
- `https://trec.nist.gov/data/trials/qrels{2021,2022,2023}.txt`
- `https://www.trec-cds.org/2021_data/ClinicalTrials.2021-04-27.part{1..5}.zip`
- `https://www.trec-cds.org/2023_data/ClinicalTrials.2023-05-08.trials{0..5}.zip`

All corpus zips serve `Accept-Ranges: bytes`, which is what makes the
`remotezip` trick work: we read the zip central directory and pull only the
~500 wanted members per topic, never the ~380 MB part file.

### 2021 — 66 unused topics

Nothing new is needed. Same corpus, same topic format, same bootstrap. The 66
remaining topics have the same judged-pool shape as the 9 in use.

### 2022 — the cheapest win

The organizers' own page says it plainly: *"The 2022 Clinical Trials track is a
direct continuation of the 2021 Clinical Trials track, with the same document
collection and task structure, only different topics."* Confirmed empirically —
all 26,585 NCTs judged in 2022 are present in the 2021-04-27 snapshot, so a
2022 task shares the exact same on-disk trial cache as the existing tasks.

`topics2022.xml` carries a stale `task="2021 TREC Clinical Trials"` root
attribute. That's an upstream typo in the XML header; the 50 topics inside are
the 2022 ones. Nothing in our code reads that attribute.

2022 has *fewer* grade-1 "excludes" judgments (3,036 vs 6,019), so its pools
skew more toward grade-0 "not relevant" distractors — slightly easier to
discard, but pool sizes land in the same 400-700 range.

### 2023 — possible, but a different task

Two real differences:

1. **Different corpus.** The 2023 track explicitly notes the collection is *"not
   the same as the 2021-2022 collection"*. Only 87.3% of the 2023 judged NCTs
   exist in the older snapshot, so using it would silently drop ~2,200 judged
   trials. `trec_data.py` therefore keys the on-disk XML cache by snapshot
   (`raw_cache_2021-04-27/` vs `raw_cache_2023-05-08/`) — the *same NCT ID has
   different content* in the two snapshots, and a shared cache would hand a task
   the wrong revision of a trial.

2. **Different topic format.** 2021/2022 topics are 5-10 sentence synthetic
   admission notes. 2023 topics are structured questionnaires: 8 condition
   templates (glaucoma, anxiety, COPD, breast cancer, COVID-19, rheumatoid
   arthritis, sickle cell anemia, type 2 diabetes) × 5 patients each, with 5-12
   `field: value` answers and some fields left blank. `extract_task_inputs.py`
   detects and renders them:

   ```
   Patient questionnaire -- glaucoma

   definitive diagnosis: primary open angle glaucoma
   intraocular pressure: (not reported)
   visual field: moderate field damage
   visual acuity: 0.3
   prior cataract surgery: no
   prior LASIK surgery: no
   comorbid ocular diseases: corneal edema
   ```

   `instruction.md` is generated with matching wording for these tasks.

Also note 2023's judgment mix is completely different: 34% of judged trials are
grade-2 (median 273 per topic) versus ~15% in 2021/2022. Pools are bigger
(~900 judged per topic), which is why `build_tasks.py` caps distractors at
`--max-pool` (default 500) so a 2023 task stays inside the agent's 1-hour budget.

**Recommendation: do 2022 and the rest of 2021 first.** They are pure volume at
zero marginal design cost and produce trajectories directly comparable to the
existing 9. Treat 2023 as a separate, deliberate variant — it's a different
enough task (sparse structured input, much denser eligible set) that mixing its
traces in with the others without labelling them would muddy any analysis.

---

## 3. What we are *not* redistributing

Same posture as upstream, preserved by the generator:

- **Committed:** topic ID, `trial_ncts.txt` (download list), `pool_ncts.txt`,
  `gold.txt`. These are NCT identifiers plus our own audit result.
- **Not committed:** the patient description (`topic.txt`) and the raw qrels.
  The bootstrap downloads `topics<YEAR>.xml` / `qrels<YEAR>.txt` and derives
  them at run time. Both paths are in `.gitignore`.
- **Never visible to the agent:** `bootstrap.sh`, `fetch_trials.py` and
  `extract_task_inputs.py` are bind-mounted into the *bootstrap* service only,
  not baked into the image the agent runs in. They name the TREC source and the
  qrels answer-key filename, which a web-capable agent could use to fetch the
  answers. Keep it that way.

---

## 4. The one hard part: choosing gold

TREC grade 2 means "eligible" *as an annotator judged relevance*, not
"a screener verified every criterion". There are 57-306 of them per 2021/2022
topic. Upstream hand-audited a handful; we reproduce that with an LLM in
`scripts/trec_ct/audit_eligible.py`.

The failure modes are asymmetric, which is what makes this tractable:

- A **false negative** (a genuinely eligible trial the auditor rejects) is
  harmless. It is dropped from the pool along with every other un-audited
  grade-2, so it can never be a hidden positive. The task just has a smaller
  gold set.
- A **false positive** (an ineligible trial promoted to gold) is fatal — it
  makes the task unpassable.

So the auditor is deliberately strict: three-way verdict
(`eligible` / `borderline` / `not_eligible`), and a candidate is promoted only
when *every* vote says `eligible` at or above `--min-confidence`.

### Calibration against upstream's human audit

Measured on 2021 topics 19, 26 and 35: 13 trials that upstream made gold, plus
18 other grade-2 trials from the same topics.

| Model | `--min-confidence` | Upstream gold recovered | Other grade-2 promoted |
| --- | --- | --- | --- |
| `claude-sonnet-5` | 0.6 | 10 / 13 | 3 / 18 |
| `claude-sonnet-5` | 0.7 | 9 / 13 | 2 / 18 |
| `claude-sonnet-5` | 0.8 | 5 / 13 | 1 / 18 |
| `claude-sonnet-5` | 0.9 | 1 / 13 | 0 / 18 |
| `claude-opus-5` | 0.6 | 10 / 13 | 3 / 18 |
| **`claude-opus-5`** | **0.7** | **10 / 13** | **2 / 18** |
| `claude-opus-5` | 0.8 | 7 / 13 | 1 / 18 |
| `claude-opus-5` | 0.9 | 4 / 13 | 1 / 18 |

Defaults are `claude-opus-5` at `0.7`. Sonnet at 0.7 is within one trial of it
for a fraction of the cost and is a reasonable choice for bulk runs.

"Other grade-2 promoted" is **not** an error rate. Upstream only audited a
sample of grade-2 per topic, so a trial they left out may well be genuinely
eligible — this column measures divergence from their sample, not wrongness.

### Observed yield

Full runs, `claude-sonnet-5` at `--min-confidence 0.7`, `--sample-per-topic 24`
with a second pass at 48 for topics that came up short:

| Year | Topics audited | Candidates audited | Promoted to gold | Tasks built |
| --- | --- | --- | --- | --- |
| 2021 | 75 | 2,009 | 433 (21.6%) | 67 / 75 |
| 2022 | 50 | 1,308 | 296 (22.6%) | 43 / 50 |

Roughly 4-6 gold per topic at 24 candidates, which lands squarely in upstream's
observed 3-9. The topics that don't make it are the ones where the auditor
finds fewer than `--min-gold` trials it will commit to; doubling the sample to
48 rescued about half of them (2021: 16 short → 8; 2022: 11 short → 7) and the
remainder did not improve, so they are left unbuilt rather than built on a
lowered bar. Relaxing `--min-confidence` would convert them, at the cost of the
one error that actually breaks a task.

Earlier small probes, kept for the 2023 signal:

| Year | Probe | Candidates audited | Promoted to gold |
| --- | --- | --- | --- |
| 2022 | topics 1-2, 10 candidates each | 20 | 6 (3 per topic) |
| 2023 | topics 1, 3, 4, 24 candidates each | 72 | 8 (1 / 1 / 6) |

2023 is the outlier, and the reason is visible in the raw verdicts: 23 of 72
came back `borderline`. Questionnaire topics answer 5-12 fields and leave some
blank, so most criteria are simply unstated and a strict auditor won't commit.
For 2023, expect to need `--sample-per-topic 48` or higher, and/or a looser
`--min-confidence`.

### Cost to enrich everything

At `--sample-per-topic 24`, one call per candidate, roughly 1k input + 200
output tokens per call (the trial XML is trimmed to just the eligibility-relevant
fields — title, summary, conditions, age/sex limits and the criteria block —
which is ~600 tokens instead of the 5-50k a raw ClinicalTrials.gov record runs):

| Batch | Topics | Calls | ≈ input tokens | ≈ output tokens |
| --- | --- | --- | --- | --- |
| 2021, remaining topics | 66 | 1,584 | 1.6M | 0.3M |
| 2022, all topics | 50 | 1,200 | 1.2M | 0.24M |
| 2023, all judged topics (at 48/topic) | 37 | 1,776 | 1.8M | 0.36M |
| **Total** | **153** | **4,560** | **4.6M** | **0.9M** |

Small enough that the model choice barely matters; use Opus.

Trial XML downloads are the other cost, and they're one-off: ~500 XMLs per
topic, but topics overlap heavily inside a year, and 2021+2022 share one cache.
Expect a few GB on disk for full enrichment.

---

## 5. Runbook

```bash
# 1. Audit grade-2 candidates down to clean-eligible gold.
#    Resumable — rerun with the same --out to continue an interrupted pass.
export ANTHROPIC_API_KEY=...
uv run python scripts/trec_ct/audit_eligible.py \
    --year 2022 --topics all --sample-per-topic 24 \
    --out assets/clinical_trial_matching/audit/2022.jsonl

# Check what it would do without spending anything:
uv run python scripts/trec_ct/audit_eligible.py --year 2022 --topics all --dry-run \
    --out /tmp/probe.jsonl

# 2. Emit Harbor task directories.
uv run python scripts/trec_ct/build_tasks.py --year 2022 \
    --audit assets/clinical_trial_matching/audit/2022.jsonl --out tasks

# 3. Fold them into the committed index. `tasks/` is gitignored, so this is the
#    step that actually persists the new tasks.
uv run python scripts/trec_ct/export_provenance.py --years 2021 2022

# 4. Verify the recipe still round-trips: rebuild everything from the index and
#    check it against the hashes recorded there.
uv run pytest tests/test_trec_ct_roundtrip.py

# 5. Run, choosing a harness by config.
HAB_LMNR_PROJECT_API_KEY=... ANTHROPIC_API_KEY=... uv run harbor run -c configs/pi.yaml
```

Anyone starting from a clean checkout skips 1–3 and just builds from the index:

```bash
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl
```

Topics that yield fewer than `--min-gold` (default 3) clean-eligible trials are
skipped with a reason printed to stderr rather than silently emitted as a
degenerate task.

Generated 2021 tasks keep upstream's `clinical_trial_matching_task_<topic>`
naming so the existing 9 round-trip; 2022/2023 tasks are named
`clinical_trial_matching_<year>_task_<topic>` so a Harbor
`--include-task-name` glob can select a single year.

---

## 6. Verification performed

- Round-trip: regenerating all 110 tasks (67 × 2021, 43 × 2022) from
  `provenance/tasks.jsonl` reproduces every file byte-for-byte —
  `diff -r --brief` against the previously committed tree is empty. That is what
  made it safe to stop committing `tasks/`. The 9 upstream tasks are the
  load-bearing ones here — their gold is Microsoft's, not ours.
- No pool in any of the 110 tasks contains an un-audited grade-2 trial, so
  `recall@top50 == 1.0` is reachable in every one of them.
- `extract_task_inputs.py` renders topic 1 correctly for all three years,
  including the 2023 questionnaire path, and writes matching per-topic qrels.
- End-to-end generation for 2022 (topic 1) and 2023 (topic 4) from real audits,
  including the correct per-year corpus zip URLs and cache mount.
- The verifier, run against a generated 2022 task:

  | Submission | reward | recall@50 | recall | precision |
  | --- | --- | --- | --- | --- |
  | gold only | 1 | 1.00 | 1.00 | 1.000 |
  | gold + 40 distractors, gold first | 1 | 1.00 | 1.00 | 0.111 |
  | gold buried at rank 60 | 0 | 0.00 | 1.00 | 0.078 |
  | whole pool dumped | 0 | 0.00 | 1.00 | 0.010 |
  | one gold missing | 0 | 0.80 | 0.80 | 1.000 |
  | gold + 2 hallucinated NCTs | 1 | 1.00 | 1.00 | 1.000 (2 discarded) |

**Not** verified: an actual `harbor run`. Docker is unavailable in the sandbox
this work was done in, so the container bootstrap path (compose ordering, bind
mounts, the `flock` cache dance) is unchanged-by-construction for 2021 and
reasoned-about for 2022, not executed.

The remote-sandbox route is one credential away. `harbor run --env daytona -a
claude-code -m claude-sonnet-5 --task tasks/clinical_trial_matching_task_19`
resolves the task, the agent and the model, and then stops at:

> Daytona requires either `DAYTONA_API_KEY`, or both `DAYTONA_JWT_TOKEN` and
> `DAYTONA_ORGANIZATION_ID`, to be set.

Harbor's Daytona backend also needs the `daytona` SDK (`uv pip install daytona`,
0.207.0 works) which is not in `pyproject.toml` — add it to the dev group if we
standardize on this route. Run a handful of tasks per year this way before
trusting the set at scale.
