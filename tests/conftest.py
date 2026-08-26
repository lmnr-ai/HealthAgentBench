"""A generated task dir to test the harnesses against.

``tasks/`` is not committed any more -- it is rebuilt from
``provenance/tasks.jsonl`` -- so the host-side agent tests can't point at a task
in git. They don't need a real one either: what they exercise is how a harness
*reads* a task dir, so the fixture builds one with the generator itself
(``build_tasks.write_task`` is pure -- it needs gold and a pool, not qrels, so
this stays offline) and hands the same files a real run would see.

Topic 6 with ``microsoft-hand-audit`` gold on purpose: that is one of upstream's
9, so the ``gold_source`` assertions are about a marker the generator has to
carry through rather than one the test invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "trec_ct"))

import build_tasks  # noqa: E402
import trec_data as td  # noqa: E402

FIXTURE_YEAR = 2021
FIXTURE_TOPIC = 6
FIXTURE_GOLD_SOURCE = build_tasks.UPSTREAM_GOLD_SOURCE
FIXTURE_GOLD = ["NCT00304863", "NCT00644046", "NCT02681068"]
FIXTURE_DISTRACTORS = [f"NCT9000000{i}" for i in range(1, 8)]


@pytest.fixture(scope="session")
def task_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("tasks")
    build_tasks.write_task(
        out,
        td.get_year(FIXTURE_YEAR),
        FIXTURE_TOPIC,
        FIXTURE_GOLD,
        sorted(FIXTURE_GOLD + FIXTURE_DISTRACTORS),
        seed=20260825,
        gold_source=FIXTURE_GOLD_SOURCE,
    )
    return out / build_tasks.task_name(td.get_year(FIXTURE_YEAR), FIXTURE_TOPIC)
