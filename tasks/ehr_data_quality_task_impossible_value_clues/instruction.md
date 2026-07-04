# EHR Data-Quality Detection

You are working inside a task environment that contains a copy of an EHR
dataset under `/workspace/data/`. Do a data quality check of the EHR
dataset and flag data entry errors belonging to certain error categories.
Submit the rows you identify at
`/workspace/submission/flagged_rows.csv`.

**You have up to 1 hour to complete this task.**

## Categories of errors to look for
You should flag all errors belonging to the following category:

1. **Impossible values** — a recorded entry whose numeric content or unit label is implausible for what it represents.
   This category was injected as the following specific error types:
   - **Physiologically impossible values** — a numeric measurement whose value lies outside the plausible physiological range for the measurement type. Look in `labevents` and `chartevents`.
   - **Decimal-shift errors** (in `labevents`, `chartevents`, or `prescriptions`).
   - **Unit-conversion mistakes** (in `labevents`).
   - **Unit-label mismatches** (in `labevents` or `chartevents`).

Be thorough and make sure to flag all such errors.

## Submission format

A CSV with these columns:

- `table`: one of patients, admissions, labevents, prescriptions,
  d_labitems, icustays, chartevents, d_items
- `_row_id`: the value of the `_row_id` column for the row you are flagging

## Data

EHR tables are at `/workspace/data/csv/<table>.csv.gz` (gzipped CSVs).

## Rules

You should not cheat and you should not directly look up for the end
solutions from the internet.
