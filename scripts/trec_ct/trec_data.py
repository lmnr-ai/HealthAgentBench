"""Shared host-side access to the TREC Clinical Trials collections.

One place that knows, per track year: where the topics / qrels / corpus live,
which corpus snapshot the year's judgments were made against, and how to pull a
single trial XML out of a ~380 MB remote zip without downloading the zip.

Verified against the live sources (see ``docs/TREC_CT_ENRICHMENT.md``):

- 2021 — 75 topics, free text, corpus ``ClinicalTrials.2021-04-27`` (375,580
  trials). 26,162 judged NCTs, 100% present in that corpus.
- 2022 — 50 topics, free text, **same** corpus as 2021. 26,585 judged NCTs,
  100% present. Drop-in: no new plumbing at all.
- 2023 — 40 topics (37 judged), questionnaire templates, corpus
  ``ClinicalTrials.2023-05-08`` (451,538 trials). 17,106 judged NCTs, 17,105
  present in the 2023 corpus but only 87.3% present in the 2021 one, so the
  2023 corpus URLs are mandatory for this year.

Everything here is a public, un-gated HTTP download. No credentials, no DUA.
"""

from __future__ import annotations

import importlib.util
import random
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
REPO_ROOT = Path(__file__).resolve().parents[2]

USER_AGENT = "healthagentbench-trec-ct/1.0 (task generation)"

#: Default host-side cache root (gitignored). Holds topics/qrels plus one
#: ``raw_cache_<snapshot>/`` per corpus snapshot, which is exactly what the
#: per-task bootstrap bind-mounts at ``/data/_cache``.
DEFAULT_CACHE_ROOT = REPO_ROOT / "assets" / "clinical_trial_matching" / "assets"

# TREC relevance grades.
GRADE_NOT_RELEVANT = 0
GRADE_EXCLUDES = 1
GRADE_ELIGIBLE = 2


def _load_extract_module():
    """Import ``templates/extract_task_inputs.py`` as a module.

    That file is the single source of truth for topic rendering: it is copied
    verbatim into every generated task and bind-mounted into the bootstrap, so
    importing it here guarantees the host-side generator and the in-container
    bootstrap render ``topic.txt`` identically.
    """
    path = TEMPLATES_DIR / "extract_task_inputs.py"
    spec = importlib.util.spec_from_file_location("_trec_extract_task_inputs", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


extract_task_inputs = _load_extract_module()
download_if_missing = extract_task_inputs.download_if_missing
parse_topics = extract_task_inputs.parse_topics
topic_templates = extract_task_inputs.topic_templates


@dataclass(frozen=True)
class Corpus:
    """A ClinicalTrials.gov snapshot published by the TREC-CDS organizers."""

    snapshot: str
    zip_urls: tuple[str, ...]

    @property
    def cache_dirname(self) -> str:
        return f"raw_cache_{self.snapshot}"


CORPUS_2021 = Corpus(
    snapshot="2021-04-27",
    zip_urls=tuple(
        f"https://www.trec-cds.org/2021_data/ClinicalTrials.2021-04-27.part{i}.zip"
        for i in range(1, 6)
    ),
)

CORPUS_2023 = Corpus(
    snapshot="2023-05-08",
    zip_urls=tuple(
        f"https://www.trec-cds.org/2023_data/ClinicalTrials.2023-05-08.trials{i}.zip"
        for i in range(6)
    ),
)


@dataclass(frozen=True)
class Year:
    """Everything year-specific about one TREC Clinical Trials track."""

    year: int
    corpus: Corpus
    topic_format: str  # "free_text" | "questionnaire"
    n_topics: int

    @property
    def topics_url(self) -> str:
        return f"https://trec.nist.gov/data/trials/topics{self.year}.xml"

    @property
    def qrels_url(self) -> str:
        return f"https://trec.nist.gov/data/trials/qrels{self.year}.txt"

    @property
    def patient_doc_phrase(self) -> str:
        """How ``instruction.md`` should describe the patient document."""
        if self.topic_format == "questionnaire":
            return "completed eligibility questionnaire"
        return "free-text admission note"

    @property
    def topic_file_phrase(self) -> str:
        if self.topic_format == "questionnaire":
            return (
                "the patient's answers to a condition-specific eligibility "
                "questionnaire, one `field: value` per line"
            )
        return "the patient case description"

    @property
    def source_phrase(self) -> str:
        if self.topic_format == "questionnaire":
            return "questionnaire answers"
        return "patient note"


YEARS: dict[int, Year] = {
    2021: Year(2021, CORPUS_2021, "free_text", 75),
    2022: Year(2022, CORPUS_2021, "free_text", 50),
    2023: Year(2023, CORPUS_2023, "questionnaire", 40),
}


def get_year(year: int) -> Year:
    if year not in YEARS:
        raise SystemExit(
            f"unsupported TREC-CT year {year}; known years: {sorted(YEARS)}"
        )
    return YEARS[year]


# ---------------------------------------------------------------------------
# Topics + qrels
# ---------------------------------------------------------------------------


def cache_root(cache_root: Path | None = None) -> Path:
    root = cache_root or DEFAULT_CACHE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def corpus_cache_dir(corpus: Corpus, cache_root_: Path | None = None) -> Path:
    """Per-snapshot NCT XML cache.

    Kept per snapshot on purpose: the same NCT ID has *different* content in the
    2021-04-27 and 2023-05-08 snapshots, so a shared cache would silently serve
    a task the wrong revision of a trial.
    """
    d = cache_root(cache_root_) / corpus.cache_dirname
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_topics(year: Year, cache_root_: Path | None = None) -> Path:
    return download_if_missing(
        year.topics_url,
        cache_root(cache_root_) / f"topics{year.year}.xml",
        USER_AGENT,
    )


def fetch_qrels(year: Year, cache_root_: Path | None = None) -> Path:
    return download_if_missing(
        year.qrels_url,
        cache_root(cache_root_) / f"qrels{year.year}.txt",
        USER_AGENT,
    )


def load_topics(year: Year, cache_root_: Path | None = None) -> dict[int, str]:
    """Topic number -> rendered patient description (same text the agent sees)."""
    return parse_topics(fetch_topics(year, cache_root_))


def load_qrels(
    year: Year, cache_root_: Path | None = None
) -> dict[int, dict[str, int]]:
    """Topic number -> {nct_id: grade}."""
    out: dict[int, dict[str, int]] = defaultdict(dict)
    for line in fetch_qrels(year, cache_root_).read_text().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        out[int(parts[0])][parts[2]] = int(parts[3])
    return dict(out)


def by_grade(judged: dict[str, int]) -> dict[int, list[str]]:
    """Split one topic's judgments into ``{grade: [nct, ...]}`` (sorted)."""
    out: dict[int, list[str]] = {
        GRADE_NOT_RELEVANT: [],
        GRADE_EXCLUDES: [],
        GRADE_ELIGIBLE: [],
    }
    for nct, grade in judged.items():
        out.setdefault(grade, []).append(nct)
    return {g: sorted(v) for g, v in out.items()}


# ---------------------------------------------------------------------------
# Trial XMLs
# ---------------------------------------------------------------------------


def fetch_trial_xmls(
    ncts: set[str],
    corpus: Corpus,
    cache_dir: Path,
    *,
    retries: int = 3,
) -> dict[str, Path]:
    """Ensure ``<NCT>.xml`` exists in ``cache_dir`` for every requested NCT.

    Cache hits cost nothing. Misses are pulled out of the remote zips with HTTP
    range requests (``remotezip``) — reading the zip central directory plus only
    the wanted members, never the whole 380 MB part.

    Returns ``{nct: path}`` for everything that resolved; missing IDs are simply
    absent from the mapping (the 2023 qrels contain exactly one NCT that is not
    in any published snapshot).
    """
    from remotezip import RemoteZip  # imported lazily: only generation needs it

    cache_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    wanted: set[str] = set()
    for nct in ncts:
        path = cache_dir / f"{nct}.xml"
        if path.is_file() and path.stat().st_size > 0:
            found[nct] = path
        else:
            wanted.add(nct)

    headers = {"User-Agent": USER_AGENT}
    for url in corpus.zip_urls:
        if not wanted:
            break
        for attempt in range(retries):
            try:
                with RemoteZip(url, headers=headers) as z:
                    hits = [
                        name
                        for name in z.namelist()
                        if name.endswith(".xml")
                        and name.rsplit("/", 1)[-1][:-4] in wanted
                    ]
                    for entry in hits:
                        nct = entry.rsplit("/", 1)[-1][:-4]
                        path = cache_dir / f"{nct}.xml"
                        with z.open(entry) as fh:
                            path.write_bytes(fh.read())
                        found[nct] = path
                        wanted.discard(nct)
                break
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                print(
                    f"[trec_data] {url}: attempt {attempt + 1} failed ({exc!r}); retrying",
                    file=sys.stderr,
                )
    return found


# Fields worth showing an eligibility auditor. Everything else in a
# ClinicalTrials.gov record (sponsors, locations, outcome measures, references)
# is noise for the "is this patient eligible" question and would multiply the
# prompt size by 10-50x.
_SUMMARY_FIELDS = (
    ("brief_title", "Title"),
    ("official_title", "Official title"),
    ("overall_status", "Status"),
    ("study_type", "Study type"),
    ("brief_summary/textblock", "Summary"),
    ("condition", "Conditions"),
    ("eligibility/gender", "Sex"),
    ("eligibility/minimum_age", "Minimum age"),
    ("eligibility/maximum_age", "Maximum age"),
    ("eligibility/healthy_volunteers", "Accepts healthy volunteers"),
    ("eligibility/criteria/textblock", "Eligibility criteria"),
)


def summarize_trial(xml_path: Path, *, max_criteria_chars: int = 8000) -> str:
    """Render the eligibility-relevant slice of a ClinicalTrials.gov XML."""
    root = ET.parse(xml_path).getroot()
    lines: list[str] = [f"NCT ID: {xml_path.stem}"]
    for path, label in _SUMMARY_FIELDS:
        values = [
            " ".join((el.text or "").split()) for el in root.findall(path)
        ]
        values = [v for v in values if v]
        if not values:
            continue
        text = "; ".join(values) if len(values) > 1 else values[0]
        if path.endswith("criteria/textblock"):
            # Keep the criteria readable: restore the list structure the
            # textblock encodes with newlines, then cap pathological records.
            raw = "\n".join(
                " ".join(line.split())
                for line in (root.findtext(path) or "").splitlines()
            )
            text = "\n".join(l for l in raw.splitlines() if l)[:max_criteria_chars]
            lines.append(f"{label}:\n{text}")
            continue
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def build_pool(
    judged: dict[str, int],
    gold: list[str],
    *,
    max_pool: int,
    seed: int,
) -> list[str]:
    """The candidate pool offered to the agent, reverse-engineered from upstream.

    Upstream's 9 committed tasks satisfy exactly::

        pool == all grade-0 + all grade-1 + gold

    i.e. every judged *non*-eligible trial is a distractor, and every grade-2
    trial that was **not** confirmed by the audit is dropped. Dropping them is
    the load-bearing part: it is what guarantees the pool contains no hidden
    positive, so an agent that finds all of ``gold`` is genuinely at recall 1.0.

    ``max_pool`` caps the distractor count (deterministically, seeded by topic)
    so 2023 topics — which have ~600 judged distractors each — stay in the same
    300-450 range as the upstream 2021 tasks and inside the agent's 1-hour
    budget. Gold is never dropped.
    """
    gold_set = set(gold)
    distractors = sorted(
        nct
        for nct, grade in judged.items()
        if grade in (GRADE_NOT_RELEVANT, GRADE_EXCLUDES) and nct not in gold_set
    )
    budget = max_pool - len(gold_set)
    if budget < 0:
        raise ValueError(f"max_pool={max_pool} is smaller than gold ({len(gold_set)})")
    if len(distractors) > budget:
        distractors = sorted(random.Random(seed).sample(distractors, budget))
    return sorted(set(distractors) | gold_set)


def copy_template(name: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATES_DIR / name, dest)


def render_template(name: str, dest: Path, values: dict[str, str]) -> None:
    text = (TEMPLATES_DIR / name).read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = [
        line for line in text.splitlines() if "{{" in line and "}}" in line
    ]
    if leftover:
        raise ValueError(f"unsubstituted placeholders in {name}: {leftover}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
