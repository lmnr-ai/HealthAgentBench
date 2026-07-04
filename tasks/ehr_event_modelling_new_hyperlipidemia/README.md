# `ehr_event_modelling_new_hyperlipidemia`

This is one of 6 `ehr_event_modelling_*` tasks in the **EHR Event Modelling** category of [HealthAgentBench](../../README.md).

Given longitudinal patient timelines and a prediction target (e.g. a future diagnosis), output a per-patient risk score; the verifier scores AUROC/AUPRC/Brier against held-out labels (Stanford SHAH lab's EHRSHOT).

**Success criteria:** reward 1.0 iff test AUROC `>= baseline_auroc`.

> **Credentials required.** EHRSHOT is Redivis-gated — set `REDIVIS_API_TOKEN` in `.env`. See [Setting up task access](../../README.md#setting-up-task-access).

## Run this task

```bash
uv run harbor run \
  --path tasks/ehr_event_modelling_new_hyperlipidemia \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Run the whole EHR Event Modelling category

Point `--path` at `tasks/` and glob the category name with `--include-task-name` (quote it so the shell doesn't expand the `*`):

```bash
uv run harbor run \
  --path tasks \
  --include-task-name "ehr_event_modelling_*" \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Data & references

- EHRSHOT (Wornow et al., 2023): <https://stanford.redivis.com/datasets/53gc-8rhx41kgt>
