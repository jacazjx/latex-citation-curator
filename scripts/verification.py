"""Field-level bibliographic checks; scores and legacy labels are not evidence."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

SCHEMA_VERSION = 2
CORE_FIELDS = ("title", "authors", "year", "venue", "publicationStatus")
DETAIL_FIELDS = ("volume", "issue", "pages", "articleNumber")
CHECK_FIELDS = CORE_FIELDS + ("doi",) + DETAIL_FIELDS + ("bibtex",)
SOURCE_FAMILIES = {
    "semantic-scholar": "semantic-scholar", "openalex": "openalex",
    "crossref": "registration", "doi-content-negotiation": "registration",
    "dblp": "dblp", "cnki": "cnki", "cnki-export": "cnki",
    "publisher-landing-page": "registration",
}
FORMAL_SOURCES = {"crossref", "dblp", "cnki", "publisher-landing-page"}


def normalize(value: Any, field: str) -> Any:
    if field == "authors":
        names = []
        for name in value or []:
            name = re.sub(r"\s+\d{4}$", "", str(name))  # DBLP disambiguation suffix
            if "," in name:
                family, given = name.split(",", 1)
                name = f"{given} {family}"
            names.append(normalize(name, "name"))
        return tuple(names)
    if field == "year":
        return str(value or "")
    text = str(value or "").strip()
    if field == "doi":
        return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I).lower()
    text = text.replace(r"\&", "&").replace("–", "-").replace("—", "-")
    text = re.sub(r"\\(?:textit|textbf|textsc|emph|mathrm|textrm)\s*", "", text)
    text = re.sub(r"\\[\"'`^~=.uvHckbdr]\s*\{?([A-Za-z])\}?", r"\1", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    if field == "pages":
        text = re.sub(r"-+", "-", text)
        return re.sub(r"\s|[{}]", "", text)
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def present(value: Any) -> bool:
    return value not in (None, "", [], (), 0)


def observation(source: str, record: dict[str, Any], *, url: str = "") -> dict[str, Any]:
    return {
        "source": source,
        "sourceUrl": url or str(record.get("url", "")),
        "values": {field: record.get(field) if record.get(field) != "unknown" else None for field in CORE_FIELDS + ("doi",) + DETAIL_FIELDS},
    }


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def bibtex_entry_key(entry: str) -> str:
    match = re.search(r"(?is)@\w+\s*\{\s*([^,\s]+)", entry)
    return normalize_whitespace(match.group(1)) if match else ""


def bibtex_field_value(entry: str, field: str) -> str:
    pattern = re.compile(rf"(?im)(?<![\w-]){re.escape(field)}\s*=")
    match = pattern.search(entry)
    if not match:
        return ""
    index = match.end()
    length = len(entry)
    while index < length and entry[index].isspace():
        index += 1
    if index >= length:
        return ""
    if entry[index] == "{":
        depth = 0
        start = index + 1
        index += 1
        while index < length:
            char = entry[index]
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    return normalize_whitespace(entry[start:index])
                depth -= 1
            index += 1
        return normalize_whitespace(entry[start:])
    if entry[index] == '"':
        start = index + 1
        index += 1
        while index < length:
            char = entry[index]
            if char == '"' and entry[index - 1] != "\\":
                return normalize_whitespace(entry[start:index])
            index += 1
        return normalize_whitespace(entry[start:])
    start = index
    while index < length and entry[index] not in {",", "\n"}:
        index += 1
    return normalize_whitespace(entry[start:index])


def split_bibtex_entries(text: str) -> list[str]:
    entries: list[str] = []
    start = None
    depth = 0
    in_quote = False
    escape = False
    for index, char in enumerate(text):
        if start is None:
            if char == "@":
                start = index
                depth = 0
                in_quote = False
                escape = False
            continue
        if in_quote:
            if char == '"' and not escape:
                in_quote = False
            escape = char == "\\" and not escape
            continue
        if char == '"' and (index == 0 or text[index - 1] != "\\"):
            in_quote = True
            escape = False
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                entries.append(text[start : index + 1].strip() + "\n")
                start = None
        escape = False
    return entries



def parse_bibtex(text: str) -> dict[str, Any]:
    entries = split_bibtex_entries(text)
    if len(entries) != 1:
        return {}
    entry = entries[0]
    def field(name):
        return bibtex_field_value(entry, name)
    return {
        "title": field("title"),
        "authors": [normalize_whitespace(name) for name in re.split(r"\s+and\s+", field("author")) if name.strip()],
        "year": field("year"),
        "venue": field("journal") or field("booktitle") or field("series"),
        "doi": normalize(field("doi"), "doi"),
        "volume": field("volume"), "issue": field("number"), "pages": field("pages"),
        "articleNumber": field("eid") or field("article-number"),
        "publicationStatus": "formal" if re.match(r"(?i)@(article|inproceedings|proceedings|book|incollection)\b", entry.strip()) else "unknown",
    }


def evaluate(record: dict[str, Any]) -> dict[str, Any]:
    """Derive state from observations and the actual downloaded/output BibTeX."""
    observations = record.get("metadataObservations", [])
    checks: dict[str, Any] = {}
    output = parse_bibtex(str(record.get("bibtex", "")))
    has_doi = present(record.get("doi")) or present(output.get("doi")) or any(
        present(o.get("values", {}).get("doi")) for o in observations
    )
    for field in CORE_FIELDS + ("doi",) + DETAIL_FIELDS:
        values = [
            {"source": o["source"], "sourceUrl": o.get("sourceUrl", ""),
             "value": o.get("values", {}).get(field)}
            for o in observations if present(o.get("values", {}).get(field))
        ]
        required = field in CORE_FIELDS or (field == "doi" and has_doi)
        if field in DETAIL_FIELDS:
            required = present(record.get(field)) or any(present(v["value"]) for v in values) or present(output.get(field))
        expected = record.get(field)
        check = {"status": "not-reported", "required": required, "selectedValue": expected, "observations": values}
        if not required:
            check["reason"] = "No DOI found; absence is not proved." if field == "doi" else "Not supplied by any observed record."
            checks[field] = check
            continue
        normalized = {normalize(v["value"], field) for v in values}
        if present(expected):
            normalized.add(normalize(expected, field))
        if not present(expected):
            check.update(status="missing", reason="No value selected for the output record.")
        elif len(normalized) > 1:
            check.update(status="conflict", reason="Observed values disagree; no value was silently discarded.")
        else:
            families = {SOURCE_FAMILIES[v["source"]] for v in values
                        if v["source"] in SOURCE_FAMILIES and v["sourceUrl"]}
            if len(families) < 2:
                check.update(status="insufficient-evidence", reason="Fewer than two distinct metadata source families agree.")
            else:
                check.update(status="passed", reason="Values agree across distinct metadata source families.")
        if field == "publicationStatus" and (expected != "formal" or record.get("preprint")):
            check.update(status="failed", reason="A formal publication has not been established.")
        if field == "publicationStatus" and check["status"] == "passed":
            formal = any(v["source"] in FORMAL_SOURCES and v["value"] == "formal" and v["sourceUrl"] for v in values)
            if not formal:
                check.update(status="insufficient-evidence", reason="No formal-publication record from a registration, publisher, DBLP or CNKI source.")
        if field == "doi":
            if not re.fullmatch(r"10\.\d{4,9}/\S+", normalize(expected, field)):
                check.update(status="failed", reason="Invalid DOI syntax.")
            elif check["status"] == "passed":
                resolution = record.get("doiResolution", {})
                resolved_same_doi = resolution.get("passed") and resolution.get("sourceUrl") and all(
                    normalize(resolution.get(key), "doi") == normalize(expected, "doi")
                    for key in ("requestedDoi", "returnedDoi")
                )
                if not resolved_same_doi:
                    check.update(status="insufficient-evidence", reason="DOI content negotiation did not return matching bibliographic data for this DOI.")
        checks[field] = check

    differences = []
    for field in ("title", "authors", "year", "venue", "doi") + DETAIL_FIELDS:
        selected, exported = record.get(field), output.get(field)
        required = field in ("title", "authors", "year", "venue") or present(selected) or present(exported)
        if required and (not present(selected) or not present(exported) or normalize(selected, field) != normalize(exported, field)):
            differences.append({"field": field, "selectedValue": selected, "bibtexValue": exported})
    bibliography_sources = [o for o in observations if o.get("role") == "bibliography"
                            and o.get("source") in {"doi-content-negotiation", "dblp", "cnki-export"} and o.get("sourceUrl")]
    checks["bibtex"] = {
        "status": "passed" if record.get("bibtex") and not differences and bibliography_sources else "failed",
        "required": True, "differences": differences,
        "sources": [{"source": o["source"], "sourceUrl": o["sourceUrl"]} for o in bibliography_sources],
        "reason": "Trusted downloaded bibliography and output match selected fields." if record.get("bibtex") and not differences and bibliography_sources
                  else "Trusted bibliography evidence is absent, or BibTeX is incomplete or differs from the selected record.",
    }
    unresolved = [field for field, check in checks.items() if check["required"] and check["status"] != "passed"]
    if not unresolved:
        status = "verified-doi" if has_doi else "verified-metadata-no-doi"
    elif any(checks[field]["status"] == "conflict" for field in unresolved):
        status = "metadata-conflict"
    elif any(checks[field]["status"] == "failed" for field in unresolved):
        status = "verification-failed"
    else:
        status = "pending-verification"
    return {
        "verificationSchemaVersion": SCHEMA_VERSION,
        "verificationScope": "bibliographic-metadata",
        "verificationStatus": status,
        "fieldChecks": checks,
        "unresolvedFields": unresolved,
        "metadataVerified": not unresolved,
        "checked": not unresolved,
        "manualCheckRequired": bool(unresolved),
        "manualCheckReasons": [f"{field}: {checks[field]['reason']}" for field in unresolved],
    }


def is_verified(record: dict[str, Any]) -> bool:
    if record.get("verificationSchemaVersion") != SCHEMA_VERSION:
        return False
    return evaluate(record)["metadataVerified"]


def apply_checks(record: dict[str, Any]) -> dict[str, Any]:
    record["bibtexMetadata"] = parse_bibtex(str(record.get("bibtex", "")))
    record.update(evaluate(record))
    if not record["metadataVerified"] and record.get("verifiedAt"):
        record["previousVerifiedAt"] = record["verifiedAt"]
        record["verifiedAt"] = ""
    record.setdefault("claimSupport", {"status": "not-reviewed", "reason": "Metadata checks and heuristic scores do not establish claim support."})
    record["scoreAssessment"] = "heuristic-not-evidence-verification"
    return record
