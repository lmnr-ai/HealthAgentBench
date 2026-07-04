"""Runtime extraction of a topic's patient note + relevance judgments.

Committing the TREC Clinical Trials 2021 topics (patient notes) and qrels
(relevance judgments) into the repo would redistribute the benchmark data.
Instead only the integer topic ID is committed per task, and the per-task
``bootstrap`` service downloads ``topics2021.xml`` + ``qrels2021.txt`` at run
time and uses this module to reconstruct, deterministically:

- the patient note  -> ``/workspace/data/topic.txt``  (agent-visible input)
- the qrels lines   -> ``/tests/qrels.txt``           (verifier-only gold)

The same parsing helpers are imported by ``generate_harbor_tasks.py`` so the
host-side generator and the in-container bootstrap stay byte-identical.

CLI (run by the bootstrap)::

    python3 extract_task_inputs.py --topic-id 35 --cache-dir /data/_cache \\
        --topic-out /workspace/data/topic.txt --qrels-out /tests/qrels.txt
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_TOPICS_URL = "https://trec.nist.gov/data/trials/topics2021.xml"
DEFAULT_QRELS_URL = "https://trec.nist.gov/data/trials/qrels2021.txt"
DEFAULT_USER_AGENT = "clinical_trial_matching/1.0 (healthagentbench benchmark)"


def _http_get(url: str, user_agent: str) -> bytes:
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted TREC URL)
        return resp.read()


def download_if_missing(
    url: str, dest: Path, user_agent: str = DEFAULT_USER_AGENT, *, retries: int = 3
) -> Path:
    """Download ``url`` to ``dest`` if it is not already cached non-empty."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            dest.write_bytes(_http_get(url, user_agent))
            return dest
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            print(
                f"[extract] {url}: attempt {attempt + 1} failed ({exc!r}); retrying in 5s",
                file=sys.stderr,
            )
            time.sleep(5)
    return dest


def parse_topics(topics_path: Path) -> dict[int, str]:
    """Map topic number -> whitespace-normalized patient-note text."""
    tree = ET.parse(topics_path)
    return {
        int(t.get("number")): " ".join((t.text or "").split())
        for t in tree.findall("topic")
    }


def parse_qrels_for_topic(qrels_path: Path, topic_id: int) -> list[tuple[str, int]]:
    """Return ``[(nct_id, grade)]`` for the given topic, in file order."""
    rows: list[tuple[str, int]] = []
    for line in Path(qrels_path).read_text().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        if int(parts[0]) != topic_id:
            continue
        rows.append((parts[2], int(parts[3])))
    return rows


def render_topic_text(topics_path: Path, topic_id: int) -> str:
    """The agent-visible ``topic.txt`` body (no trailing newline, matching the
    historical committed format)."""
    topics = parse_topics(topics_path)
    if topic_id not in topics:
        raise SystemExit(f"[extract] topic {topic_id} not found in {topics_path}")
    return topics[topic_id]


def render_qrels(qrels_path: Path, topic_id: int) -> str:
    """The verifier-only ``qrels.txt`` body (``<topic> 0 <nct> <grade>`` lines,
    trailing newline) — byte-identical to what the generator used to commit."""
    judged = parse_qrels_for_topic(qrels_path, topic_id)
    if not judged:
        raise SystemExit(f"[extract] topic {topic_id} has no qrels rows")
    return "\n".join(f"{topic_id} 0 {nct} {grade}" for nct, grade in judged) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-id", type=int, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help="Where topics2021.xml / qrels2021.txt are cached / downloaded.",
    )
    parser.add_argument("--topic-out", type=Path, required=True)
    parser.add_argument("--qrels-out", type=Path, required=True)
    parser.add_argument("--topics-url", default=DEFAULT_TOPICS_URL)
    parser.add_argument("--qrels-url", default=DEFAULT_QRELS_URL)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)

    topics_path = download_if_missing(
        args.topics_url, args.cache_dir / "topics2021.xml", args.user_agent
    )
    qrels_path = download_if_missing(
        args.qrels_url, args.cache_dir / "qrels2021.txt", args.user_agent
    )

    args.topic_out.parent.mkdir(parents=True, exist_ok=True)
    args.qrels_out.parent.mkdir(parents=True, exist_ok=True)
    # topic.txt: no trailing newline (matches the historical committed file).
    args.topic_out.write_text(render_topic_text(topics_path, args.topic_id), encoding="utf-8")
    args.qrels_out.write_text(render_qrels(qrels_path, args.topic_id), encoding="utf-8")
    print(
        f"[extract] topic {args.topic_id}: wrote {args.topic_out} "
        f"and {args.qrels_out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
