# Patient-to-Trial Eligibility (Identify All)

You are working inside an environment that contains a single patient's
free-text admission note and a directory of candidate clinical-trial
documents. **Identify all trials in the directory that the patient is
eligible for** -- meaning the trial is relevant to the patient and the patient meets **all** of the trial's
inclusion criteria *and* **none** of its exclusion criteria.

## Inputs

- `/workspace/data/topic.txt` -- the patient case description.
- `/workspace/data/topic_id.txt` -- the integer topic ID for this task.
- `/workspace/data/trials/<NCT_ID>.xml` -- one file per candidate trial.
  Each XML follows the standard ClinicalTrials.gov schema.

## Output

Write a plain text file at
`/workspace/submission/eligible_trials.txt` containing **one NCT
identifier per line**
You should identify **all** trials the patient is eligible for, and
**no** trials the patient is excluded from or unrelated to. List the
trials in descending order of your confidence.

Format example:

    NCT00012345
    NCT00067890
    NCT00111222

Blank lines and lines starting with `#` are ignored. Duplicates are
de-duplicated.


## Rules

Solve the task using the patient note and the trial documents in
`/workspace/data/trials/`, applying standard medical reasoning over the
patient's clinical history. Do not search the internet for benchmark
answers.

**You have up to 1 hour to complete this task.**
