# `scripts/trec_ct` — TREC Clinical Trials task generation

Tooling to build `clinical_trial_matching_*` Harbor tasks for any TREC Clinical
Trials topic (2021, 2022 or 2023). Upstream's generator was never published;
this reproduces it from the 9 committed tasks.

Read **[../../docs/TREC_CT_ENRICHMENT.md](../../docs/TREC_CT_ENRICHMENT.md)**
first — it has the reverse-engineered recipe, the per-year data survey, the
auditor calibration numbers and the cost estimates.

| File | What it is |
| --- | --- |
| `trec_data.py` | Year/corpus registry, topic + qrels loading, remote-zip trial fetch, trial summarization, pool construction. |
| `audit_eligible.py` | LLM pass that narrows TREC grade-2 trials to a *clean-eligible* gold set. Resumable, seeded, `--dry-run`-able. |
| `build_tasks.py` | Emits Harbor task directories from an audit or from the committed index. |
| `export_provenance.py` | Writes `provenance/tasks.jsonl` — the committed index `build_tasks.py --index` reads back. |
| `templates/` | The canonical task files. Everything under `tasks/` is generated from here. |

## `tasks/` is generated, and not committed

Harbor gives a task directory no way to share files: each one must be
self-contained, so 110 tasks means 110 identical Dockerfiles, compose files,
bootstrap scripts and evaluators. Harbor's answer to that is the adapter
pattern — commit the generator and the inputs, not the output — which is what
this directory is. So `tasks/` is gitignored, and rebuilt on demand:

```bash
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl
```

`--index` reads the committed `provenance/tasks.jsonl`, which carries each
task's gold NCTs, its `gold_source` marker and a SHA-256 of the `gold.txt` /
`pool_ncts.txt` it must produce. That makes the rebuild verifiable rather than
merely repeatable: `tests/test_trec_ct_roundtrip.py` rebuilds all 110 and checks
every hash.

Edit `templates/`, then regenerate — don't hand-edit a task directory, it is
not tracked and will be silently overwritten. After changing anything that
alters gold or pool contents, re-export the index or the hashes will (correctly)
fail the round-trip test.

`templates/extract_task_inputs.py` is doubly load-bearing: it is copied verbatim
into every task *and* imported by `trec_data.py`, so the host-side generator and
the in-container bootstrap can never disagree about how `topic.txt` is rendered.

## Two things not to break

1. **Un-audited grade-2 trials must stay out of the pool.** They are TREC-judged
   eligible but unverified, so leaving one in the pool creates a hidden positive
   and makes `recall@top50 == 1.0` unreachable. `trec_data.build_pool` enforces
   this; don't "fix" it by adding them back.
2. **`bootstrap.sh`, `fetch_trials.py` and `extract_task_inputs.py` are
   bind-mounted into the bootstrap service only**, never baked into the image
   the agent runs in. They name the TREC source and the qrels answer-key file. A
   web-capable agent that could read them could fetch the answers.

## Usage

```bash
export ANTHROPIC_API_KEY=...

# See what would be audited, without spending anything
uv run python scripts/trec_ct/audit_eligible.py --year 2022 --topics all \
    --dry-run --out /tmp/probe.jsonl

# Audit (resumable: rerun with the same --out to continue)
uv run python scripts/trec_ct/audit_eligible.py --year 2022 --topics all \
    --sample-per-topic 24 \
    --out assets/clinical_trial_matching/audit/2022.jsonl

# Generate tasks from a fresh audit
uv run python scripts/trec_ct/build_tasks.py --year 2022 \
    --audit assets/clinical_trial_matching/audit/2022.jsonl --out tasks

# Fold them into the committed index
uv run python scripts/trec_ct/export_provenance.py --years 2021 2022

# Rebuild the whole dataset from that index (the usual entry point)
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl

# Regression check: every task rebuilds to its committed hashes
uv run pytest tests/test_trec_ct_roundtrip.py
```

Downloads land in `assets/clinical_trial_matching/assets/` (gitignored):
`topics<YEAR>.xml`, `qrels<YEAR>.txt`, and one `raw_cache_<snapshot>/` per
corpus snapshot — which is exactly the directory each task's
`docker-compose.yaml` bind-mounts at `/data/_cache`.
