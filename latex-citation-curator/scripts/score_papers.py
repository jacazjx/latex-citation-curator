#!/usr/bin/env python3
"""Rank candidate papers from local metadata."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

CCF_SCORES = {"A": 35.0, "B": 28.0, "C": 18.0}
JCR_SCORES = {"Q1": 30.0, "Q2": 22.0, "Q3": 14.0, "Q4": 8.0}


def read_records(path_str: str) -> list[dict[str, object]]:
    if path_str == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_str).read_text(encoding="utf-8")

    stripped = raw.strip()
    if not stripped:
        return []

    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array of objects.")
        return [normalize_record(item) for item in data]

    records = []
    for line in stripped.splitlines():
        if not line.strip():
            continue
        records.append(normalize_record(json.loads(line)))
    return records


def normalize_record(record: object) -> dict[str, object]:
    if not isinstance(record, dict):
        raise ValueError("Every candidate record must be a JSON object.")
    return dict(record)


def parse_float(record: dict[str, object], key: str, default: float = 0.0) -> float:
    value = record.get(key, default)
    if value in (None, "", "unknown"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_int(record: dict[str, object], key: str, default: int = 0) -> int:
    value = record.get(key, default)
    if value in (None, "", "unknown"):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_bool(record: dict[str, object], key: str) -> bool:
    value = record.get(key, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def normalize_ccf(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("CCF-", "").replace("CCF ", "")
    return text if text in CCF_SCORES else None


def normalize_jcr(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if text in JCR_SCORES else None


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def compute_scores(
    record: dict[str, object],
    current_year: int,
    allow_preprint: bool,
) -> dict[str, object]:
    year = parse_int(record, "year")
    citation_count = parse_int(record, "citationCount")
    impact_factor = parse_float(record, "impactFactor")
    relevance_score = clamp(parse_float(record, "relevanceScore"), 0.0, 10.0)
    evidence_score = clamp(parse_float(record, "evidenceScore"), 0.0, 10.0)
    doi = str(record.get("doi", "") or "").strip()
    source = str(record.get("source", "") or "").strip()
    source_url = str(record.get("sourceUrl", "") or "").strip()
    reliable_bibtex_source = str(record.get("reliableBibtexSource", "") or "").strip()
    peer_reviewed = parse_bool(record, "peerReviewed")
    preprint = parse_bool(record, "preprint")
    has_formal_version = parse_bool(record, "hasFormalVersion")

    ccf_tier = normalize_ccf(record.get("ccfTier"))
    jcr_quartile = normalize_jcr(record.get("jcrQuartile"))
    venue_score = max(
        CCF_SCORES.get(ccf_tier, 0.0),
        JCR_SCORES.get(jcr_quartile, 0.0),
        10.0 if peer_reviewed else 0.0,
    )

    age = max(0, current_year - year) if year else current_year
    freshness_score = max(0.0, 20.0 - 4.0 * max(age - 1, 0))

    denominator = max(1, current_year - year + 1) if year else 1
    citations_per_year = citation_count / denominator
    citation_score = min(15.0, 5.0 * math.log10(1.0 + citation_count))
    citation_score += min(10.0, 4.0 * math.log10(1.0 + citations_per_year))

    impact_score = min(15.0, 6.0 * math.log10(1.0 + impact_factor))
    publication_bonus = 0.0
    if source and source_url and not preprint:
        if doi:
            publication_bonus = 5.0
        elif reliable_bibtex_source:
            publication_bonus = 3.0

    rejection_reasons: list[str] = []
    if not doi and not reliable_bibtex_source:
        rejection_reasons.append("missing-doi-and-no-trusted-bibtex-source")
    if not source or not source_url:
        rejection_reasons.append("missing-provenance")
    if preprint and has_formal_version:
        rejection_reasons.append("replace-with-formal-version")
    if preprint and not has_formal_version and not allow_preprint:
        rejection_reasons.append("preprint-not-allowed")

    quality_score = (
        venue_score
        + freshness_score
        + citation_score
        + impact_score
        + relevance_score
        + evidence_score
        + publication_bonus
    )

    result = dict(record)
    result.update(
        {
            "qualityScore": round(quality_score, 2),
            "venueScore": round(venue_score, 2),
            "freshnessScore": round(freshness_score, 2),
            "citationScore": round(citation_score, 2),
            "impactScore": round(impact_score, 2),
            "relevanceScore": round(relevance_score, 2),
            "evidenceScore": round(evidence_score, 2),
            "publicationBonus": round(publication_bonus, 2),
            "citationsPerYear": round(citations_per_year, 2),
            "eligible": not rejection_reasons,
            "rejectionReasons": rejection_reasons,
        }
    )
    return result


def render_tsv(records: list[dict[str, object]]) -> str:
    headers = [
        "eligible",
        "qualityScore",
        "title",
        "year",
        "venue",
        "doi",
        "ccfTier",
        "jcrQuartile",
        "citationCount",
        "citationsPerYear",
        "rejectionReasons",
    ]
    lines = ["\t".join(headers)]
    for record in records:
        row = []
        for header in headers:
            value = record.get(header, "")
            if isinstance(value, list):
                value = ",".join(str(item) for item in value)
            row.append(str(value))
        lines.append("\t".join(row))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        help="Path to JSON/JSONL input, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "tsv"),
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--allow-preprint",
        action="store_true",
        help="Keep preprints that have no verified formal version.",
    )
    parser.add_argument(
        "--current-year",
        type=int,
        default=datetime.now(UTC).year,
        help="Override the year used by freshness and citations-per-year scoring.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Keep only the top N records after sorting. Use 0 for all records.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = read_records(args.input)
    scored = [
        compute_scores(record, args.current_year, args.allow_preprint)
        for record in records
    ]
    scored.sort(
        key=lambda item: (
            0 if item["eligible"] else 1,
            -float(item["qualityScore"]),
            -(parse_int(item, "year")),
        )
    )
    if args.top > 0:
        scored = scored[: args.top]

    if args.format == "tsv":
        sys.stdout.write(render_tsv(scored) + "\n")
    else:
        json.dump(scored, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
