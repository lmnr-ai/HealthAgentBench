# CLAUDE.md — HealthAgentBench (Laminar fork)

Fork of [microsoft/HealthAgentBench](https://github.com/microsoft/HealthAgentBench),
stripped down to the one dataset we are licensed to use: **TREC Clinical Trials**
patient-to-trial matching (`tasks/clinical_trial_matching_*`). We run it to
harvest agent **trajectories** (Laminar traces), not for leaderboard scores.

Everything else upstream shipped (CT-RATE, MIMIC-CXR, MIMIC-IV, EHRSHOT,
Camelyon 16) was deleted along with the leaderboard `website/` and its
GitHub Pages deploy workflow — do not restore them, the data is gated.

## Layout

| Path | What |
| --- | --- |
| `tasks/clinical_trial_matching_*` | **Generated output, gitignored.** Harbor 0.8.0 task dirs. Build them before running anything. |
| `provenance/tasks.jsonl` | **The dataset.** One row per task: gold NCTs, `gold_source`, and the hashes a rebuild has to reproduce. |
| `scripts/trec_ct/` | The generator: year registry, LLM auditor, task builder, index exporter. |
| `scripts/trec_ct/templates/` | **Source of truth** for every file inside a task. |
| `provenance/audit_*.jsonl` | Every audit verdict, per candidate trial. |
| `scripts/harbor_agents/` | The trajectory harnesses (`trajectory.py` is the shared half) and the trace verifier. |
| `configs/` | Harbor job configs, one per harness — how a run picks its harness and model. |
| `docs/TREC_CT_ENRICHMENT.md` | Reverse-engineered recipe, per-year survey, cost estimates, runbook. |

Read `docs/TREC_CT_ENRICHMENT.md` before touching anything under `scripts/trec_ct`.

## Invariants that are easy to break

- **`tasks/` is generated and not in git.** 110 tasks × 15 near-identical files
  is 1,650 wholly derivable files, and Harbor gives a task dir no way to share
  anything — each must be self-contained, so there is no template/include
  primitive to reach for. Its answer is the adapter pattern: commit the
  generator and its input, generate the output. Rebuild with

  ```bash
  uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl
  ```

  Never hand-edit a task directory — it is untracked, so an edit is lost at the
  next rebuild with nothing in `git status` to warn you. Edit
  `scripts/trec_ct/templates/` instead, then regenerate and run
  `pytest tests/test_trec_ct_roundtrip.py`: it rebuilds all 110 from the index
  and checks each against the `gold_sha256` / `pool_sha256` recorded there.
  Dropping `tasks/` from git was verified by rebuilding and `diff -r --brief`
  against the previously committed tree (110/110 byte-identical) — redo that if
  you change the generator in a way that could alter output.
- **Un-audited TREC grade-2 trials must stay out of the candidate pool.** The
  pool is `all grade-0 + all grade-1 + audited gold`. A grade-2 left in the pool
  is a hidden positive and makes the task's `recall@top50 == 1.0` unpassable.
- **Regenerating a task must never relabel its gold.** Each `task.toml` carries
  `metadata.gold_source` — `microsoft-hand-audit` for upstream's original 9 (2021
  topics 6, 8, 19, 26, 27, 29, 35, 45, 75), `llm-audit:<model>` for ours. Task
  *names* don't distinguish them: 2021 tasks all use upstream's bare naming.
  `--index` and `--gold-from-existing` both read the existing marker back and
  preserve it; only `--audit` sets a new one. `export_provenance.py` and
  `build_tasks.py --index` are inverses, so after any change to `tasks/` that
  isn't a plain rebuild, re-export or the recorded hashes go stale:
  `python scripts/trec_ct/export_provenance.py --years 2021 2022`.
- **Never commit topics or qrels.** `topic.txt` (the patient description) and
  `qrels.txt` (the answer key) are TREC-redistributable-by-nobody; each task
  downloads them at run time. Only NCT IDs and our own audit output are tracked.
- **`bootstrap.sh`, `fetch_trials.py` and `extract_task_inputs.py` are
  bind-mounted into the `bootstrap` service only**, never baked into the agent's
  image — they name the TREC source URLs and the qrels filename, so a web-capable
  agent that could read them could fetch the answers. The trajectory agent probes
  for `/tests/gold.txt` on every trial and refuses to record a trajectory if it
  is reachable, so a bad compose edit fails the run instead of quietly producing
  trajectories where the model could read the answers.
- `extract_task_inputs.py` is imported by `trec_data.py` *and* copied into every
  task, so host-side generation and in-container bootstrap can never disagree
  about how `topic.txt` is rendered. Keep it dependency-free (stdlib only).

## Generating trajectories

A **harness** is the agent scaffold whose behaviour a trajectory reflects. Two
ship, and which one runs is a config file, not a command line:

| Config | Harness | Class | Where the agent runs |
| --- | --- | --- | --- |
| `configs/laminar-bash.yaml` | `custom/laminar-bash-loop` | `laminar_bash_agent:LaminarBashAgent` | Host — we drive the tool loop ourselves |
| `configs/pi.yaml` | `pi` | `pi_agent:LaminarPiAgent` | Sandbox — Pi CLI, traced in-process by `@lmnr-ai/pi-extension` |

`trajectory.py` holds everything that must **not** vary between them: the
metadata schema, host-side scoring, the answer-key probe, Laminar init. A new
harness subclasses `TrajectoryAgent` alongside its Harbor base class and sets
`HARNESS`. `harness` is the only metadata key that is supposed to differ —
`tests/test_pi_agent.py` pins that.

```bash
uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl
HAB_LMNR_PROJECT_API_KEY=... AZURE_API_KEY=... \
  uv run harbor run -c configs/pi.yaml --job-name sliceN
HAB_LMNR_PROJECT_API_KEY=... uv run python scripts/harbor_agents/verify_traces.py \
  --minutes 10 --key-var HAB_LMNR_PROJECT_API_KEY
uv run python scripts/harbor_agents/trace_manifest.py jobs/sliceN \
  -o jobs/sliceN/trace_manifest.json
```

`tests/test_configs.py` parses every shipped config, resolves its `import_path`
through Harbor's own factory and checks it names a `TrajectoryAgent` — cheap
insurance against a typo that would otherwise surface 95 minutes into a batch.

Non-obvious things that cost time:

- **Pin `lmnr_key_var`, don't rely on `LMNR_PROJECT_API_KEY`.** That name is
  generic enough that the surrounding environment (a Laminar coding sandbox, for
  one) often already exports one, and the run will happily write an entire batch
  of trajectories into somebody else's project. Both shipped configs set it to
  `HAB_LMNR_PROJECT_API_KEY` under `agents[].kwargs`, and `tests/test_configs.py`
  fails any config that doesn't. The agent logs the var it used and the key's
  first 6 chars, so a misroute is at least diagnosable after the fact —
  `verify_traces.py --key-var` must be given the same var.
- **`agents[].env` in a job config reaches an agent as an `extra_env` kwarg, and
  `BaseAgent.__init__` swallows unknown kwargs.** So a host-side agent that
  doesn't claim it silently gets no credentials from its own config file, with
  no error. `TrajectoryAgent.__init__` keeps it as `self._extra_env` and
  forwards it, and `self._env(name)` reads config-env first, then `os.environ`.
  Look up every credential through `_env`, never `os.environ` directly.
- **`Laminar.initialize(set_global_tracer_provider=False)` is required.** The
  Daytona SDK self-instruments via `trace.get_tracer()` on the *global* provider
  (`daytona/_utils/otel_decorator.py`); claiming that provider exports a stray
  single-span root trace for every SDK call (`AsyncFileSystem.search_files`,
  `AsyncSandbox.delete`, …) — 136 of them in one 1-task run. Restricting
  `instruments={Instruments.OPENAI}` does **not** prevent it; Laminar's own
  instrumentors resolve through its `TracerWrapper`, so OpenAI spans are
  unaffected by opting out.
- **`openai` is capped below 3.0 by harbor's litellm pin**, not by us.
- Azure's OpenAI-compatible `/openai/v1` surface authenticates on an `api-key`
  header, which the OpenAI SDK never sends; the agent adds it when the base URL
  contains `azure`. (A bearer `Authorization` header works there too, which is
  what Pi sends.) `gpt-5.6-luna` and `gpt-5.6-terra` also need
  `max_completion_tokens`.
- **Pi reaches a model its registry has never heard of through `pi_models`.**
  Anything OpenAI-, Anthropic- or Google-compatible — every Azure Foundry
  deployment included — is a provider block in `agents[].kwargs.pi_models`,
  which is *verbatim* Pi's own `~/.pi/agent/models.json` schema (documented in
  `docs/models.md` of `@mariozechner/pi-coding-agent`), written into the sandbox
  at setup. Don't invent a second schema for it: Pi already resolves `apiKey` as
  a shell command, an env-var name, or a literal, and the config wants the
  *name* so no key lands in git — `tests/test_configs.py` fails a config whose
  `apiKey` isn't a variable that `env` also ships. `model_name` must be
  `<provider>/<model>` and name a provider the block defines; `_validate_pi_models`
  raises at construction rather than letting the typo surface as a dead trial.
- **Azure + tools + a reasoning model means `api: openai-responses`.**
  `/chat/completions` returns 400 `Function tools with reasoning_effort are not
  supported ... use /v1/responses` for `gpt-5.6-terra`, and Pi always sends a
  reasoning effort for a model it has marked `reasoning`. Our bash loop is
  unaffected because it sends no `reasoning_effort` at all.
- **`pi --version` prints to *stderr*.** `BaseInstalledAgent.setup` reads only
  `stdout` and swallows the miss, so harbor stamps every Pi trajectory
  `harness_version: unknown`. `_detect_pi_version` re-runs it with `2>&1`. Pi is
  installed `@latest`, so this is the only record of which Pi ran.
- **`model` metadata carries the model, not the route to it.** Pi requires
  `<provider>/<model>` and the bash loop takes a bare deployment name, so
  recording `model_name` verbatim filed one Azure deployment under two model
  names and broke the only cross-harness comparison these trajectories are for.
  `_model_metadata` splits the prefix into `model_provider`.
- `harbor run -i <task>` is rejected without `-p tasks` (or `-d`/`-t`), which is
  how you run a slice: `-p tasks -i clinical_trial_matching_task_6 -n 1`.
- **`harness` is the agent scaffold, not harbor.** `custom/laminar-bash-loop`
  or `pi` — whatever actually shaped the trajectory. Harbor provisions the
  sandbox and computes the reward without influencing a single step, so it lives
  in the flat `runner` / `runner_version` keys. Consumers group trajectories by
  `harness`, so a new harness must also get a distinct `name()`: returning the
  stock Harbor agent name would make `-a pi` and `LaminarPiAgent` runs
  indistinguishable in the `agent` key while producing different records.
- **Trace depth is per-harness, and `verify_traces.py` enforces it.** The bash
  loop is 2 levels (root → flat `LLM` / `TOOL` siblings); Pi is 3, because the
  extension opens its own `pi agent run` span under our root and hangs the steps
  off that. `EXPECTED_MAX_DEPTH` in `verify_traces.py` pins both; an unlisted
  harness gets its depth reported and a WARN, not a pass.
- **An in-sandbox harness needs `LMNR_SPAN_CONTEXT`, or its trace is orphaned.**
  `Laminar.initialize` in the TS SDK adopts that env var as the active parent
  (`_initializeContextFromEnv`), so `LaminarPiAgent` serializes the host root
  span into it and injects it — plus the project key *renamed to the canonical
  `LMNR_PROJECT_API_KEY`*, which is the only name the extension reads — through
  `self._extra_env`, which `BaseInstalledAgent._exec` merges into every sandbox
  command. Without it the extension fails open: pi runs fine, a separate trace
  appears in the project, and the host has no id to stamp metadata on or write
  into `result.json`. Silent, so `_sandbox_laminar_env` raises instead.
- **Pi's `--mode json` output is megabytes and is needed while the root span is
  still open**, before Harbor syncs `/logs`. `PI_SUMMARY_SCRIPT` reduces it to
  counters *in the sandbox*; it is kept as source text rather than a file so
  `tests/test_pi_agent.py` runs the exact same program against synthetic
  streams. Count `message_end` events with `role == "assistant"` for steps —
  event count is several times higher, and `toolResult` messages are not turns.
- **Pi's setup installs nvm + node 22 + two npm packages in the sandbox**, which
  does not reliably fit Harbor's 360 s default agent-setup timeout at
  concurrency. `configs/pi.yaml` sets `override_setup_timeout_sec: 900`.
- **`gt_event_identified` is the error flag, not the pass flag** — `true` means
  the answer is *wrong*. The trajectories train a model to spot mistakes and
  inefficiencies, so the event being identified is the mistake. `passed` is
  carried alongside it for the unsurprising reading. Inverting this is silent
  and poisons every label, so `tests/test_laminar_bash_agent.py` pins it.
- **"We never read the submission" is not "the submission was empty."** Pi
  writes its answer inside the sandbox, and `_finalize` reads it back from
  `run()`'s `finally` — which on a timeout runs with the task already
  cancelled, so the `cat` gets cancelled too. Scoring the `""` left over would
  stamp `gt_event_identified: true` on a run whose answer may be sitting on
  disk. `_score_and_record` takes `submission_text=None` for that case and
  records no verdict plus `submission_readback_failed`; only the harnesses that
  write the submission themselves (the bash loop) may treat `""` as an answer.
- The agent scores its own submission by importing the task's *own*
  `tests/harbor_evaluator.py` host-side, so the verdict can't drift from
  Harbor's reward. Verified identical on every slice so far — if you change
  one, don't reimplement the other.
- Daytona's DinD strategy handles the 2-service compose, but the raw-XML cache
  bind mount escapes the task dir and doesn't exist on the VM, so `bootstrap.sh`
  falls through to its network download path. Costs ~1 min/task, nothing else.
- Harbor's `DaytonaClientManager._cleanup_sync` atexit hook raises
  `CancelledError` after a successful run, and the process can sit there instead
  of exiting. Both are noise: `finished_at` in `jobs/<name>/result.json` is set
  before it, so the run is complete and the process is safe to kill. Don't wait
  on `harbor run` exiting to decide a batch is done — poll that file.
- **Budget for a retry pass on any batch run: `-n 8` loses ~12% of trials to
  `RuntimeError: docker compose up failed`** while the DinD sandbox brings the
  two-service compose up. It is pure infrastructure flake — the trials die in
  environment setup, before `run()`, so they leave a `result.json` with an
  `exception_info` and an *empty* `agent_result.metadata`, and no trace. Re-run
  just those task names at `-n 4`, then any survivors at `-n 1`; the full 110
  took 8 + 4 + 1 to clear (110/110, ~95 min total). Both configs now set
  `retry.max_retries: 2` to absorb it in-run — Harbor's default
  `exclude_exceptions` keeps timeouts and reward-file failures out of the retry,
  so a genuinely failed trajectory is still recorded as failed. Never read a
  batch's pass rate off the trial count alone — count trials whose metadata is
  non-empty:

  ```python
  md = (json.load(open(p))["agent_result"] or {}).get("metadata") or {}
  ```
- **One Laminar project per run, and check the trace count afterwards.** A
  project is not a run: `verify_traces.py` reports every trace in the look-back
  window, so a second run into the same project silently interleaves with the
  first and there is no metadata key that separates them (same `source`,
  `harness`, `model`). Each trial records its own `lmnr_trace_id` in
  `result.json`, so the run's own traces are exactly the ids in
  `jobs/<name>/trace_manifest.json` — build that manifest and hand it over with
  the run, rather than a project name plus a time window.

## Environment

- **No credentials are needed** to build or run the tasks — TREC topics, qrels
  and the ClinicalTrials.gov snapshot are all public. `ANTHROPIC_API_KEY` is only
  needed to run `audit_eligible.py`; `AZURE_API_KEY` to run either harness
  against the shipped configs' Azure Foundry deployment (Pi resolves it inside
  the sandbox, which is why `configs/pi.yaml` lists it under `agents[].env`);
  `HAB_LMNR_PROJECT_API_KEY` only to trace runs.
- **`tasks/` will not exist in a fresh checkout.** Build it before running
  anything that reads a task dir, including the agent tests' fixture path:
  `uv run python scripts/trec_ct/build_tasks.py --index provenance/tasks.jsonl`.
  (`tests/conftest.py` generates its own throwaway task, so `pytest` itself does
  not need it.)
- The XML cache is keyed by corpus snapshot (`raw_cache_2021-04-27`,
  `raw_cache_2023-05-08`) because the same NCT ID has different content in
  different snapshots. 2021 and 2022 share the 2021-04-27 snapshot; 2023 does not.
- Trials are pulled out of the ~380 MB remote zips with `remotezip` HTTP range
  requests — do not add a step that downloads the whole archive.
- Running a task needs Docker (`harbor run`). The Laminar coding sandbox has no
  Docker, so task *generation* and the verifier can be validated there but an
  end-to-end task run cannot.

## Style

Python 3.12, `uv` for envs, `ruff` for lint.
`uvx ruff check scripts tests tasks configs` must pass (`tasks` only after a
build; it is generated). Note `[tool.ruff.lint] extend-select` in `pyproject.toml` exists so the
`# noqa` directives in the task files stay live — `ruff check --fix` will silently
strip a `# noqa: X` whose rule `X` isn't selected.
