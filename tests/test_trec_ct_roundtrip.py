"""Regression test for the reverse-engineered task recipe.

Regenerating the 9 committed TREC-CT 2021 tasks from their own ``gold.txt`` must
reproduce ``pool_ncts.txt`` and ``gold.txt`` byte-for-byte. That is the whole
evidence that ``build_tasks.py`` implements upstream's construction —

    pool == every judged grade-0 + every judged grade-1 + gold

— and not something that merely looks similar. If this test goes red, any task
generated for 2022/2023 is suspect too.

``trial_ncts.txt`` is only checked as a *set*: it is the bootstrap's download
list and is deliberately shuffled, and we don't know upstream's shuffle seed.

Needs ``qrels2021.txt``, which is downloaded once into
``assets/clinical_trial_matching/assets/`` and reused. Skips (rather than fails)
if TREC is unreachable and nothing is cached.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.error import URLError

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "trec_ct"
TASKS = REPO_ROOT / "tasks"

sys.path.insert(0, str(SCRIPTS))

import build_tasks  # noqa: E402
import trec_data as td  # noqa: E402


def _read(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("roundtrip")
    try:
        argv = [
            "--year", "2021",
            "--gold-from-existing", str(TASKS),
            "--out", str(out),
        ]
        build_tasks.main(argv)
    except (URLError, OSError) as exc:  # pragma: no cover - offline CI
        pytest.skip(f"TREC qrels unavailable: {exc!r}")
    return out


def _committed_tasks() -> list[Path]:
    return sorted(TASKS.glob("clinical_trial_matching_task_*"))


def test_there_are_committed_tasks_to_check():
    assert len(_committed_tasks()) == 9


@pytest.mark.parametrize("task_dir", _committed_tasks(), ids=lambda p: p.name)
def test_pool_and_gold_roundtrip(task_dir: Path, regenerated: Path):
    rebuilt = regenerated / task_dir.name
    assert rebuilt.is_dir(), f"{task_dir.name} was not regenerated"

    assert (rebuilt / "tests" / "gold.txt").read_bytes() == (
        task_dir / "tests" / "gold.txt"
    ).read_bytes()
    assert (rebuilt / "tests" / "pool_ncts.txt").read_bytes() == (
        task_dir / "tests" / "pool_ncts.txt"
    ).read_bytes()
    assert set(_read(rebuilt / "environment" / "workspace" / "trial_ncts.txt")) == set(
        _read(task_dir / "environment" / "workspace" / "trial_ncts.txt")
    )


@pytest.mark.parametrize("task_dir", _committed_tasks(), ids=lambda p: p.name)
def test_pool_holds_no_unaudited_grade_2(task_dir: Path):
    """The invariant that makes recall@top50 == 1.0 reachable at all."""
    try:
        qrels = td.load_qrels(td.get_year(2021))
    except (URLError, OSError) as exc:  # pragma: no cover - offline CI
        pytest.skip(f"TREC qrels unavailable: {exc!r}")

    topic_id = int(
        (task_dir / "environment" / "workspace" / "topic_id.txt").read_text().strip()
    )
    gold = set(_read(task_dir / "tests" / "gold.txt"))
    pool = set(_read(task_dir / "tests" / "pool_ncts.txt"))
    judged = qrels[topic_id]

    hidden = {
        nct
        for nct in pool - gold
        if judged.get(nct) == td.GRADE_ELIGIBLE
    }
    assert not hidden, f"topic {topic_id}: hidden positives in pool: {sorted(hidden)}"
    assert gold <= pool
    assert all(judged.get(nct) == td.GRADE_ELIGIBLE for nct in gold)
