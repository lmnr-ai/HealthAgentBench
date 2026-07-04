# `ehr_to_meds_etl`

This is the single task in the **EHR Format Conversion** category of [HealthAgentBench](../../README.md).

Points the agent at the `MIMIC_IV_MEDS` codebase and a demo raw MIMIC-IV input, and asks it to run / repair the ETL pipeline so the produced MEDS cohort validates (MIMIC-IV → MEDS common data format).

**Success criteria:** binary pass/fail — the produced MEDS cohort passes all ETL validation checks.

## Run this task

```bash
uv run harbor run \
  --path tasks/ehr_to_meds_etl \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 1
```

## Data & references

Uses the bundled MIMIC-IV **demo** input — no credentials required.

- MEDS (Medical Event Data Standard): <https://github.com/Medical-Event-Data-Standard/meds>
- MIMIC-IV-demo v2.2: <https://physionet.org/content/mimiciv-demo/2.2/>
