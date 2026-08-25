<p align="center">
  <img src="assets/banner.png" alt="HealthAgentBench banner">
</p>

# HealthAgentBench — Laminar fork (Clinical Trial Matching only)

This is [lmnr-ai](https://github.com/lmnr-ai)'s fork of
[microsoft/HealthAgentBench](https://github.com/microsoft/HealthAgentBench).

We use it to **generate agent trajectories** (Laminar traces) by rerunning the
benchmark with new models. The success rate is secondary; the traces are the
product.

## What changed vs. upstream

Upstream ships **54 tasks across 7 categories**. Six of those categories are
built on datasets we are not licensed to use (PhysioNet credentialed MIMIC-CXR /
MIMIC-IV, Hugging Face-gated CT-RATE, Redivis-gated EHRSHOT), and one
(Camelyon 16 pathology slides) is licensed but is a whole-slide-image task we
don't want. So this fork keeps exactly one category:

| Category | # Tasks | Dataset | Access |
| --- | --- | --- | --- |
| Clinical Trial Matching (`clinical_trial_matching`) | 9 | TREC Clinical Trials 2021 | Public, un-gated |

Removed in this fork: `xray_report_correction`, `ct_abnormality`,
`ehr_data_quality`, `ehr_event_modelling`, `ehr_to_meds_etl`,
`tumor_area_selection_pathology`, the upstream leaderboard site (`website/`,
`.github/workflows/deploy.yml`), and every credential / dependency that existed
only to serve those categories.

**No credentials are required to run anything in this fork.** TREC topics,
relevance judgments (qrels) and the ClinicalTrials.gov corpus snapshots are all
public downloads from `trec.nist.gov` and `www.trec-cds.org`.

## The task

Given one patient admission note plus a per-topic candidate pool of ~300–450
clinical-trial XML documents, identify **every** trial the patient is eligible
for and write one NCT per line, in descending order of confidence, to
`/workspace/submission/eligible_trials.txt`.

**Success criterion:** `recall@top50 == 1.0`.

The verifier also reports `recall`, `recall_top_20`, `precision` and `f1` in
`verifier/metrics.json`.

## Setup

```bash
git clone https://github.com/lmnr-ai/HealthAgentBench.git
cd HealthAgentBench
uv sync --all-extras     # Python >= 3.12
```

No `.env` is needed to *run* tasks. `.env.example` documents the (optional) keys
used for agent auth, Laminar tracing, and task *generation*.

## Usage

Run the whole suite:

```bash
uv run harbor run \
  --path tasks \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 1 --n-concurrent 5 \
  --jobs-dir <output directory>
```

Run a single task:

```bash
uv run harbor run \
  --path tasks/clinical_trial_matching_task_19 \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 1 --n-concurrent 1
```

Notes:

1. The first run downloads the per-topic trial XMLs into
   `assets/clinical_trial_matching/assets/raw_cache/` (gitignored) via HTTP
   range requests against the upstream zip snapshot. Subsequent runs are
   offline-fast. Budget ~1–2 GB for the full 9-task cache.
2. Gold labels for these 9 tasks *are* committed (`tests/gold.txt`). The patient
   note (`topic.txt`) and the raw qrels are **not** — the bootstrap derives them
   at run time so we don't redistribute TREC data.
3. Keep web search / web fetch disabled so the agent can't look up the answers.

## Growing the benchmark

The 9 committed tasks are a small slice of what TREC Clinical Trials offers:

| Track year | Topics | Format | Corpus | Reusable here? |
| --- | --- | --- | --- | --- |
| 2021 | 75 (9 used) | Free-text admission note | `ClinicalTrials.2021-04-27` | **Yes — 66 more topics, zero new plumbing** |
| 2022 | 50 | Free-text admission note | *Same* `ClinicalTrials.2021-04-27` | **Yes — drop-in, zero new plumbing** |
| 2023 | 40 (37 judged) | Structured questionnaire fields | `ClinicalTrials.2023-05-08` (different) | Yes, with a topic renderer + new corpus URLs |

`scripts/trec_ct/` contains everything needed to build new tasks for any of
those years. See **[docs/TREC_CT_ENRICHMENT.md](docs/TREC_CT_ENRICHMENT.md)** for
the reverse-engineered task recipe, the per-year findings, and cost estimates.

Quick start:

```bash
# 1. Audit which TREC grade-2 trials are *cleanly* eligible -> gold candidates
uv run python scripts/trec_ct/audit_eligible.py --year 2022 --topics 1-50 \
  --sample-per-topic 24 --out assets/clinical_trial_matching/audit/2022.jsonl

# 2. Emit Harbor task directories from the audit
uv run python scripts/trec_ct/build_tasks.py --year 2022 \
  --audit assets/clinical_trial_matching/audit/2022.jsonl --out tasks
```

## Harbor background

This project uses [Harbor](https://github.com/harbor-framework/harbor) `0.8.0`
as the terminal-task execution and evaluation substrate. Docs:
<https://deepwiki.com/harbor-framework/harbor>.

## Data references

- TREC Clinical Trials 2021: <https://www.trec-cds.org/2021.html>
- TREC Clinical Trials 2022: <https://www.trec-cds.org/2022.html>
- TREC Clinical Trials 2023: <https://www.trec-cds.org/2023.html>

## Citation

Upstream benchmark:

```bibtex
@misc{liu2026healthagentbench,
  title = {HealthAgentBench: A Unified Benchmark Suite of Realistic Agentic Healthcare Environments for Challenging Frontier AI Agents},
  author = {Liu, Qianchu and Zhang, Sheng and Qin, Guanghui and Valanarasu, Jeya Maria Jose and Rokuss, Maximilian and Lu, Mingyu and Ossowski, Timothy and Chaves, Juan Manuel Zambrano and Wong, Cliff and Argaw, Peniel and Hasija, Yashna and Wei, Mu and Yim, Wen-wai and Liu, Qin and Jing, Zilin and Entenmann, Jason and Usuyama, Naoto and Naumann, Tristan and Poon, Hoifung},
  year = {2026}
}
```
