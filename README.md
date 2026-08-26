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
| Clinical Trial Matching (`clinical_trial_matching_task_*`) | 67 | TREC Clinical Trials 2021 | Public, un-gated |
| Clinical Trial Matching (`clinical_trial_matching_2022_task_*`) | 43 | TREC Clinical Trials 2022 | Public, un-gated |

Upstream shipped 9 of these (2021 topics 6, 8, 19, 26, 27, 29, 35, 45, 75) and
no generator. The other 101 were built with `scripts/trec_ct/` — see
[docs/TREC_CT_ENRICHMENT.md](docs/TREC_CT_ENRICHMENT.md). The original 9 keep
Microsoft's hand-audited gold and are byte-for-byte unchanged.

Upstream also committed each task directory. This fork generates them instead
(see [Build the tasks](#build-the-tasks)) and adds a configurable trajectory
harness in `scripts/harbor_agents/`.

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

### Build the tasks

`tasks/` is **generated, not committed** — 110 near-identical Harbor task
directories are not worth 1,650 files in git. What is committed is the input
they are built from, `provenance/tasks.jsonl`, plus the templates:

```bash
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl
```

That is offline apart from the trial-XML fetch, deterministic, and reproduces
every task byte-for-byte (`tests/test_trec_ct_roundtrip.py` asserts it).

## Usage

Which harness runs the benchmark is a config file, not a command line. Two ship:

| Config | Harness | Where the agent runs |
| --- | --- | --- |
| `configs/laminar-bash.yaml` | `custom/laminar-bash-loop` | A bash tool-loop we drive from the host |
| `configs/pi.yaml` | `pi` | Pi (`@mariozechner/pi-coding-agent`) inside the sandbox, traced by `@lmnr-ai/pi-extension` |

```bash
HAB_LMNR_PROJECT_API_KEY=... ANTHROPIC_API_KEY=... \
  uv run harbor run -c configs/pi.yaml
```

Both write the same trajectory record to Laminar; only the `harness` metadata
key differs, so trajectories from the two are directly comparable. Copy a config
to add a model or a harness — see `scripts/harbor_agents/trajectory.py` for what
a new harness has to implement (in practice: a class, one constant).

Any stock Harbor agent works too, if you only want the score:

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

Notes:

1. The first run downloads the per-topic trial XMLs into
   `assets/clinical_trial_matching/assets/raw_cache/` (gitignored) via HTTP
   range requests against the upstream zip snapshot. Subsequent runs are
   offline-fast. Budget ~1–2 GB for the full 9-task cache.
2. Gold labels *are* committed, in `provenance/tasks.jsonl` and in each
   generated `tests/gold.txt`. The patient note (`topic.txt`) and the raw qrels
   are **not** — the bootstrap derives them at run time so we don't
   redistribute TREC data.
3. Keep web search / web fetch disabled so the agent can't look up the answers.

## Growing the benchmark

| Track year | Topics | Format | Corpus | Status |
| --- | --- | --- | --- | --- |
| 2021 | 75 | Free-text admission note | `ClinicalTrials.2021-04-27` | **67 built** (8 topics yielded < 3 audited-eligible trials) |
| 2022 | 50 | Free-text admission note | *Same* `ClinicalTrials.2021-04-27` | **43 built** (7 short of `--min-gold`) |
| 2023 | 40 (37 judged) | Structured questionnaire fields | `ClinicalTrials.2023-05-08` (different) | Supported by the tooling, not built yet |

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

# 3. Fold the new tasks into the committed index, so others can rebuild them
uv run python scripts/trec_ct/export_provenance.py --years 2021 2022
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
