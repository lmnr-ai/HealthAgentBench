# Tumor Area Selection

You are working inside a pathology task environment that contains:

- the current whole-slide image at `/data/slide/current/slide.*`
- a public task row in `/workspace/benchmark_tasks.json`
- an editable single-row submission in `/workspace/submission.json`

The benchmark analysis grid uses 256x256 tiles at downsample 16, so each grid tile spans 4096x4096 full-resolution (level-0) pixels (`4096 = 256*16`). Grid tile `(x, y)` covers the level-0 region with horizontal extent `x*4096` to `(x+1)*4096` (x = column) and vertical extent `y*4096` to `(y+1)*4096` (y = row). The grid has `ceil(slide_width/4096)` columns (indexed by x) and `ceil(slide_height/4096)` rows (indexed by y). Report tumor tiles using these integer `(x, y)` grid coordinates.

## Your Task

Your task is to identify all and only the tiles that contain tumor and include them in the submission.
- populate `predicted_tumor_tiles` with dictionaries of the form `{ "x": <int>, "y": <int> }`
- include every tile you believe contains tumor and do not include tiles you believe do not contain tumor
- non-tissue tiles should be treated as non-tumor
- do not modify `task_id` or `instruction`

## Rules

- You have a budget of 1.5 hours to complete this task.
- Be mindful of your time budget: work efficiently, avoid unnecessary or repetitive analysis, and make sure you finish and write your submission well within the time limit.
- You should not cheat and you should not directly look up for the end solutions from the internet.
- Work autonomously until the submission is complete.
- Do not train models or fine-tune weights.
- Update `submission.json` with a JSON-aware tool such as Python.
