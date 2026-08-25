# CLAUDE.md — HealthAgentBench (Laminar fork)

Fork of [microsoft/HealthAgentBench](https://github.com/microsoft/HealthAgentBench),
stripped down to the one dataset we are licensed to use: **TREC Clinical Trials**
patient-to-trial matching (`tasks/clinical_trial_matching_*`). We run it to
harvest agent **trajectories** (Laminar traces), not for leaderboard scores.

Everything else upstream shipped (CT-RATE, MIMIC-CXR, MIMIC-IV, EHRSHOT,
Camelyon 16) was deleted along with the leaderboard `website/` and its
GitHub Pages deploy workflow — do not restore them, the data is gated.

## Layout

| Path | What |
| --- | --- |
| `tasks/clinical_trial_matching_*` | **Generated output.** Harbor 0.8.0 task dirs. |
| `scripts/trec_ct/` | The generator: year registry, LLM auditor, task builder. |
| `scripts/trec_ct/templates/` | **Source of truth** for every file inside a task. |
| `provenance/` | Committed record of who audited each task's gold, and every verdict. |
| `docs/TREC_CT_ENRICHMENT.md` | Reverse-engineered recipe, per-year survey, cost estimates, runbook. |

Read `docs/TREC_CT_ENRICHMENT.md` before touching anything under `scripts/trec_ct`.

## Invariants that are easy to break

- **Never hand-edit a `tasks/` directory.** Edit `scripts/trec_ct/templates/`
  and regenerate: `python scripts/trec_ct/build_tasks.py --year 2021
  --gold-from-existing tasks --out tasks`. Then run
  `pytest tests/test_trec_ct_roundtrip.py` — it asserts the 9 committed 2021
  tasks regenerate byte-identically, which is the only evidence the recipe is
  actually upstream's.
- **Un-audited TREC grade-2 trials must stay out of the candidate pool.** The
  pool is `all grade-0 + all grade-1 + audited gold`. A grade-2 left in the pool
  is a hidden positive and makes the task's `recall@top50 == 1.0` unpassable.
- **Regenerating a task must never relabel its gold.** Each `task.toml` carries
  `metadata.gold_source` — `microsoft-hand-audit` for upstream's original 9 (2021
  topics 6, 8, 19, 26, 27, 29, 35, 45, 75), `llm-audit:<model>` for ours. Task
  *names* don't distinguish them: 2021 tasks all use upstream's bare naming.
  `--gold-from-existing` reads each task's existing marker back and preserves it;
  only `--audit` sets a new one. Re-export the index after any change to `tasks/`:
  `python scripts/trec_ct/export_provenance.py --years 2021 2022`.
- **Never commit topics or qrels.** `topic.txt` (the patient description) and
  `qrels.txt` (the answer key) are TREC-redistributable-by-nobody; each task
  downloads them at run time. Only NCT IDs and our own audit output are tracked.
- **`bootstrap.sh`, `fetch_trials.py` and `extract_task_inputs.py` are
  bind-mounted into the `bootstrap` service only**, never baked into the agent's
  image — they name the TREC source URLs and the qrels filename, so a web-capable
  agent that could read them could fetch the answers.
- `extract_task_inputs.py` is imported by `trec_data.py` *and* copied into every
  task, so host-side generation and in-container bootstrap can never disagree
  about how `topic.txt` is rendered. Keep it dependency-free (stdlib only).

## Environment

- **No credentials are needed** to build or run the tasks — TREC topics, qrels
  and the ClinicalTrials.gov snapshot are all public. `ANTHROPIC_API_KEY` is only
  needed to run `audit_eligible.py`; `LMNR_PROJECT_API_KEY` only to trace runs.
- The XML cache is keyed by corpus snapshot (`raw_cache_2021-04-27`,
  `raw_cache_2023-05-08`) because the same NCT ID has different content in
  different snapshots. 2021 and 2022 share the 2021-04-27 snapshot; 2023 does not.
- Trials are pulled out of the ~380 MB remote zips with `remotezip` HTTP range
  requests — do not add a step that downloads the whole archive.
- Running a task needs Docker (`harbor run`). The Laminar coding sandbox has no
  Docker, so task *generation* and the verifier can be validated there but an
  end-to-end task run cannot.

## Style

Python 3.12, `uv` for envs, `ruff` for lint. `uvx ruff check scripts tests tasks`
must pass. Note `[tool.ruff.lint] extend-select` in `pyproject.toml` exists so the
`# noqa` directives in the task files stay live — `ruff check --fix` will silently
strip a `# noqa: X` whose rule `X` isn't selected.
