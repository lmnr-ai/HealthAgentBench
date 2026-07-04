# `ct_abnormality_valid_636_a_1`

This is one of 10 `ct_abnormality_*` tasks in the **CT Abnormality Classification** category of [HealthAgentBench](../../README.md).

Given one chest-CT volume plus a list of requested abnormalities, decide **yes/no for each** and write the predictions (patient-level, built on CT-RATE). Gold labels are derived from the paired radiology report and hand-verified.

**Success criteria:** reward 1.0 iff **every** requested abnormality label is correct.

> **Credentials required.** CT-RATE is Hugging Face OpenRAIL-gated — set `HF_TOKEN` in `.env`. See [Setting up task access](../../README.md#setting-up-task-access).

## Run this task

```bash
uv run harbor run \
  --path tasks/ct_abnormality_valid_636_a_1 \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Run the whole CT Abnormality Classification category

Point `--path` at `tasks/` and glob the category name with `--include-task-name` (quote it so the shell doesn't expand the `*`):

```bash
uv run harbor run \
  --path tasks \
  --include-task-name "ct_abnormality_*" \
  --agent claude-code \
  --model claude-opus-4-8 \
  --agent-kwarg reasoning_effort=xhigh \
  --agent-kwarg disallowed_tools="WebSearch WebFetch" \
  --n-attempts 3 --n-concurrent 5
```

## Data & references

- CT-RATE (Hamamci et al., 2024): <https://huggingface.co/datasets/ibrahimhamamci/CT-RATE>
