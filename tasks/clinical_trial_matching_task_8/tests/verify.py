#!/usr/bin/env python3
"""Per-task verifier entry point. Calls harbor_evaluator.evaluate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harbor_evaluator import evaluate  # noqa: E402


def main() -> None:
    here = Path(__file__).resolve().parent
    submission = Path("/workspace/submission/eligible_trials.txt")
    gold = here / "gold.txt"
    pool = here / "pool_ncts.txt"
    log_dir = Path("/logs/verifier")
    score = evaluate(submission, gold, pool, log_dir)
    print(f"pass={score:.0f}")


if __name__ == "__main__":
    main()
