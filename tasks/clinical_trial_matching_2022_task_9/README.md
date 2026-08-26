# `clinical_trial_matching_2022_task_9`

This is one of the `clinical_trial_matching_*` tasks in the **Clinical Trial Matching** category of [HealthAgentBench](../../README.md).

Given one patient free-text admission note plus a per-topic candidate pool of clinical-trial documents, identify every trial the patient is eligible for and write one NCT per line in descending order of confidence (TREC Clinical Trials 2022, topic 9).

**Success criteria:** recall@top50 == 1.0.

## Run this task

```bash
uv run harbor run \
  --path tasks/clinical_trial_matching_2022_task_9 \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Run the whole Clinical Trial Matching category

Point `--path` at `tasks/` and glob the category name with `--include-task-name` (quote it so the shell doesn't expand the `*`):

```bash
uv run harbor run \
  --path tasks \
  --include-task-name "clinical_trial_matching_*" \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Data & references

The candidate pool for each topic is an audited subset: the clean-eligible trials (gold) plus non-eligible distractors. Pool size 500, gold size 5.

**Gold provenance: `llm-audit:claude-sonnet-5`.** Every gold trial was graded eligible (grade 2) by TREC's human assessors first; the audit step is a second, stricter filter over those. See [`provenance/`](../../provenance/README.md).

- TREC Clinical Trials 2022: <https://www.trec-cds.org/2022.html>
