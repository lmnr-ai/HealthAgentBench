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

import json
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


def _write_audit(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_load_audit_ignores_other_years(tmp_path: Path):
    """Topic numbers restart every track, so gold must be keyed on (year, topic)."""
    audit = _write_audit(
        tmp_path / "mixed.jsonl",
        [
            {"year": 2021, "topic_id": 12, "nct_id": "NCT00000001", "clean_eligible": True},
            {"year": 2022, "topic_id": 12, "nct_id": "NCT00000002", "clean_eligible": True},
            {"year": 2022, "topic_id": 12, "nct_id": "NCT00000003", "clean_eligible": False},
        ],
    )
    assert build_tasks.load_audit(audit, td.get_year(2022)) == {12: ["NCT00000002"]}
    assert build_tasks.load_audit(audit, td.get_year(2021)) == {12: ["NCT00000001"]}
    # An audit for a year we aren't building yields nothing rather than mis-keyed gold.
    assert build_tasks.load_audit(audit, td.get_year(2023)) == {}


def test_build_rejects_gold_that_is_not_grade_2_for_the_year(tmp_path: Path):
    """The guard that catches a gold source built against a different year."""
    try:
        qrels = td.load_qrels(td.get_year(2021))
    except (URLError, OSError) as exc:  # pragma: no cover - offline CI
        pytest.skip(f"TREC qrels unavailable: {exc!r}")

    not_eligible = sorted(
        nct for nct, grade in qrels[19].items() if grade != td.GRADE_ELIGIBLE
    )[:4]
    audit = _write_audit(
        tmp_path / "wrong.jsonl",
        [
            {"year": 2021, "topic_id": 19, "nct_id": nct, "clean_eligible": True}
            for nct in not_eligible
        ],
    )
    with pytest.raises(SystemExit, match="not.*grade-2"):
        build_tasks.main(
            ["--year", "2021", "--audit", str(audit), "--out", str(tmp_path / "out")]
        )
