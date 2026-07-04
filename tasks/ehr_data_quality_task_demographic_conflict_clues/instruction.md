# EHR Data-Quality Detection

You are working inside a task environment that contains a copy of an EHR
dataset under `/workspace/data/`. Do a data quality check of the EHR
dataset and flag data entry errors belonging to certain error categories.
Submit the rows you identify at
`/workspace/submission/flagged_rows.csv`.

**You have up to 1 hour to complete this task.**

## Categories of errors to look for
You should flag all errors belonging to the following category:

1. **Demographic contradictions** — the patient's recorded demographic information contradicts other evidence about that patient.
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
