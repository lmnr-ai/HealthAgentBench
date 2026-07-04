# `ehr_data_quality_task_demographic_conflict_clues`

This is one of 8 `ehr_data_quality_*` tasks in the **EHR Data Quality Auditing** category of [HealthAgentBench](../../README.md).

Given a corrupted EHR dataset (a MIMIC-IV-demo subset, source name hidden), flag the rows containing deliberately-injected data-quality errors — impossible values, conflicting/duplicate records, and demographic contradictions — writing `(table, _row_id)` rows.

**Success criteria:** a trial passes iff `recall == 1.0` AND `precision >= 0.01`.

## Run this task

```bash
uv run harbor run \
  --path tasks/ehr_data_quality_task_demographic_conflict_clues \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Run the whole EHR Data Quality Auditing category

Point `--path` at `tasks/` and glob the category name with `--include-task-name` (quote it so the shell doesn't expand the `*`):

```bash
uv run harbor run \
  --path tasks \
  --include-task-name "ehr_data_quality_*" \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Data & references
MIMIC-IV-demo is public — no credentials required.

- MIMIC-IV-demo v2.2: <https://physionet.org/content/mimiciv-demo/2.2/>
