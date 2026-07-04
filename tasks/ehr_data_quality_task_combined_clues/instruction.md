# EHR Data-Quality Detection

You are working inside a task environment that contains a copy of an EHR
dataset under `/workspace/data/`. Do a data quality check of the EHR
dataset and flag data entry errors belonging to certain error categories.
Submit the rows you identify at
`/workspace/submission/flagged_rows.csv`.

**You have up to 1 hour to complete this task.**

## Categories of errors to look for
You should flag all errors belonging to the following categories:

1. **Impossible values** — a recorded entry whose numeric content or unit label is implausible for what it represents.
   This category was injected as the following specific error types:
   - **Physiologically impossible values** — a numeric measurement whose value lies outside the plausible physiological range for the measurement type. Look in `labevents` and `chartevents`.
   - **Decimal-shift errors** (in `labevents`, `chartevents`, or `prescriptions`).
   - **Unit-conversion mistakes** (in `labevents`).
   - **Unit-label mismatches** (in `labevents` or `chartevents`).
2. **Conflicting records** — two records that should describe the same observation for the same patient at the same time but disagree.
   This category was injected as the following specific error types:
   - **Within-table conflicts** — two rows in the same table that should agree on a measurement but record disagreeing values. Look within `labevents` and `chartevents`.
   - **Cross-table conflicts** — two rows in different tables that record the same conceptual measurement for the same patient at the same time, but with disagreeing values (between `labevents` and `chartevents`).
3. **Demographic contradictions** — the patient's recorded demographic information contradicts other evidence about that patient.
   This category was injected as the following specific error types:
   - **Patient-gender mismatches** — the patient's recorded gender in `patients` contradicts other in-record entries. The error could be wrong entries from `patients` table, wrong entries from `prescriptions` table, or wrong entries from `labevents`.
   - **Age contradictions** — a row in `patients` whose recorded age contradicts other evidence about that patient (look at `prescriptions`)

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
