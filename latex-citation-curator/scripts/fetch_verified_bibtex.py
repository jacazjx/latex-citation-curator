#!/usr/bin/env python3
"""Search, verify, score, and emit DOI-verified or trusted-source BibTeX entries."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from score_papers import compute_scores

SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "year",
        "venue",
        "citationCount",
        "url",
        "publicationDate",
        "publicationTypes",
        "authors",
        "externalIds",
        "journal",
        "publicationVenue",
    ]
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}

EMPIRICAL_TERMS = {
    "ablation",
    "benchmark",
    "dataset",
    "empirical",
    "evaluation",
    "experiment",
    "experimental",
    "results",
    "study",
}

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
TRUSTED_BIBTEX_SOURCES = {"dblp", "doi-content-negotiation"}
LOCAL_REUSE_SOURCES = {"local-bib", "local-ledger", "user-library"}


class FetchError(RuntimeError):
    """Raised when a remote metadata source fails."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


NETWORK_CACHE: "PersistentCache | None" = None
PROGRESS_TRACKER: "ProgressTracker | None" = None


def current_date_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def codex_home_path() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def secret_root_path() -> Path:
    override = os.environ.get("CODEX_SECRET_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    codex_home = codex_home_path()
    if str(codex_home).startswith("/mnt/"):
        return Path.home() / ".codex-secrets"
    return codex_home / "secrets"


def secret_store_path() -> Path:
    return secret_root_path() / "latex-citation-curator.json"


def cache_root_path() -> Path:
    override = os.environ.get("CODEX_CACHE_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    codex_home = codex_home_path()
    if str(codex_home).startswith("/mnt/"):
        return Path.home() / ".cache" / "latex-citation-curator"
    return codex_home / "cache" / "latex-citation-curator"


def cache_store_path() -> Path:
    return cache_root_path() / "network-cache.json"


def user_library_path() -> Path:
    return cache_root_path() / "paper-library.json"


def project_state_dir(project_root: Path) -> Path:
    return project_root / ".latex-citation-curator"


def project_ledger_path(project_root: Path) -> Path:
    return project_state_dir(project_root) / "verification-ledger.json"


class PersistentCache:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        self.entries = payload.get("entries", {}) if isinstance(payload, dict) else {}

    def get(self, key: str) -> Any | None:
        entry = self.entries.get(key)
        if not isinstance(entry, dict):
            return None
        return entry.get("payload")

    def set(self, key: str, payload: Any) -> None:
        self.entries[key] = {"updated_at": current_date_iso(), "payload": payload}
        self.save()

    def save(self) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        body = {"entries": self.entries}
        tmp_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)


def truncate_box_text(text: str, width: int) -> str:
    text = normalize_whitespace(text)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


class ProgressTracker:
    BOX_WIDTH = 70

    def __init__(self, *, total_queries: int, enabled: bool = True):
        self.total_queries = total_queries
        self.enabled = enabled
        self.current_query = 0
        self.stage = "Starting"
        self.detail = ""
        self.mode = "auth"
        self.cache_hits = 0
        self.network_fetches = 0
        self.retries = 0

    def update(self, *, current_query: int | None = None, stage: str | None = None, detail: str | None = None, mode: str | None = None) -> None:
        if current_query is not None:
            self.current_query = current_query
        if stage is not None:
            self.stage = stage
        if detail is not None:
            self.detail = detail
        if mode is not None:
            self.mode = mode
        self.render()

    def bump_cache_hit(self) -> None:
        self.cache_hits += 1
        self.render()

    def bump_network_fetch(self) -> None:
        self.network_fetches += 1
        self.render()

    def bump_retry(self, *, detail: str) -> None:
        self.retries += 1
        self.detail = detail
        self.render()

    def render(self) -> None:
        if not self.enabled:
            return
        inner = self.BOX_WIDTH - 4
        lines = [
            "+" + "-" * (self.BOX_WIDTH - 2) + "+",
            f"| {truncate_box_text('LaTeX Citation Curator Progress', inner):<{inner}} |",
            f"| {truncate_box_text(f'Query {self.current_query}/{self.total_queries} | Mode: {self.mode}', inner):<{inner}} |",
            f"| {truncate_box_text(f'Stage: {self.stage}', inner):<{inner}} |",
            f"| {truncate_box_text(self.detail or '-', inner):<{inner}} |",
            f"| {truncate_box_text(f'Cache hits: {self.cache_hits} | Network fetches: {self.network_fetches} | Retries: {self.retries}', inner):<{inner}} |",
            "+" + "-" * (self.BOX_WIDTH - 2) + "+",
        ]
        print("\n".join(lines), file=sys.stderr, flush=True)


def load_stored_api_key() -> str:
    path = secret_store_path()
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("semantic_scholar_api_key", "")).strip()


def save_stored_api_key(api_key: str) -> Path:
    path = secret_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "semantic_scholar_api_key": api_key,
        "updated_at": current_date_iso(),
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_verification_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": current_date_iso(), "entries": {}, "bibFiles": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    entries = payload.get("entries", {})
    bib_files = payload.get("bibFiles", {})
    return {
        "version": 1,
        "updated_at": str(payload.get("updated_at", current_date_iso())),
        "entries": entries if isinstance(entries, dict) else {},
        "bibFiles": bib_files if isinstance(bib_files, dict) else {},
    }


def save_verification_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = current_date_iso()
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def load_user_library(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": current_date_iso(), "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    entries = payload.get("entries", {})
    return {
        "version": 1,
        "updated_at": str(payload.get("updated_at", current_date_iso())),
        "entries": entries if isinstance(entries, dict) else {},
    }


def save_user_library(path: Path, library: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    library["updated_at"] = current_date_iso()
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def resolve_semantic_scholar_key(args: argparse.Namespace) -> tuple[str, str, str]:
    provided_api_key = str(args.semantic_scholar_api_key or "").strip()
    stored_api_key = load_stored_api_key()
    if provided_api_key:
        return provided_api_key, provided_api_key, "provided"
    if stored_api_key:
        return stored_api_key, "", "stored"
    if args.no_key_prompt or not sys.stdin.isatty():
        print("No Semantic Scholar API key found. Continuing with the free shared mode.", file=sys.stderr)
        return "", "", "shared"

    prompt = (
        "Semantic Scholar API key available? Paste it now for authenticated mode, "
        "or press Enter to continue with the free shared mode: "
    )
    print(prompt, file=sys.stderr, end="", flush=True)
    entered_api_key = sys.stdin.readline().strip()
    if entered_api_key:
        return entered_api_key, entered_api_key, "prompt"

    print("Continuing with the free shared mode.", file=sys.stderr)
    return "", "", "shared"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return normalize_whitespace(text)


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", text.casefold())
    return {token for token in tokens if token not in STOPWORDS}


def build_search_query(claim: str) -> str:
    claim_text = normalize_whitespace(claim)
    phrase_candidates = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)+", claim_text)
    selected: list[str] = []
    seen: set[str] = set()

    for phrase in phrase_candidates:
        lowered = phrase.casefold()
        if lowered not in seen:
            selected.append(phrase)
            seen.add(lowered)

    token_candidates = re.findall(r"[a-z0-9]+", claim_text.casefold())
    for token in token_candidates:
        if token in STOPWORDS or len(token) < 3:
            continue
        if token not in seen:
            selected.append(token)
            seen.add(token)
        if len(selected) >= 12:
            break

    return " ".join(selected) if selected else claim_text


def normalize_doi(value: str) -> str:
    text = normalize_whitespace(value)
    if not text:
        return ""
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    return text.strip()


def title_similarity(left: str, right: str) -> float:
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    token_left = tokenize(left_norm)
    token_right = tokenize(right_norm)
    jaccard = len(token_left & token_right) / max(1, len(token_left | token_right))
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return 0.6 * sequence + 0.4 * jaccard


def overlap_score(query: str, title: str, abstract: str) -> float:
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0.0
    title_tokens = tokenize(title)
    abstract_tokens = tokenize(abstract)
    title_overlap = len(query_tokens & title_tokens) / len(query_tokens)
    abstract_overlap = len(query_tokens & abstract_tokens) / len(query_tokens)
    return min(1.0, 0.75 * title_overlap + 0.25 * abstract_overlap)


def infer_relevance_score(query: str, title: str, abstract: str) -> float:
    similarity = title_similarity(query, title)
    overlap = overlap_score(query, title, abstract)
    return min(10.0, round(10.0 * (0.6 * similarity + 0.4 * overlap), 2))


def infer_evidence_score(title: str, abstract: str, peer_reviewed: bool) -> float:
    haystack = f"{title} {abstract}".casefold()
    score = 2.0 if peer_reviewed else 1.0
    score += 2.0 * bool(re.search(r"\b(survey|review|systematic)\b", haystack))
    score += 2.0 * bool(re.search(r"\b(theory|theoretical|proof)\b", haystack))
    score += 2.0 * bool(any(term in haystack for term in EMPIRICAL_TERMS))
    score += 2.0 * bool(re.search(r"\b(state[- ]of[- ]the[- ]art|sota|outperform)\b", haystack))
    return min(10.0, score)


def choose_year(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def http_get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    cache_key: str | None = None,
    retryable_codes: set[int] | None = None,
    max_retries: int = 2,
    retry_forever: bool = False,
    initial_backoff: int = 5,
    max_backoff: int = 300,
    progress_label: str = "network request",
) -> str:
    if cache_key and NETWORK_CACHE is not None:
        cached = NETWORK_CACHE.get(cache_key)
        if cached is not None:
            if PROGRESS_TRACKER is not None:
                PROGRESS_TRACKER.bump_cache_hit()
            return str(cached)

    retryable_codes = retryable_codes or set()
    attempt = 0
    backoff = initial_backoff

    while True:
        request = Request(url, headers=headers or {})
        try:
            with urlopen(request, timeout=timeout) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(encoding, errors="replace")
                if cache_key and NETWORK_CACHE is not None:
                    NETWORK_CACHE.set(cache_key, body)
                if PROGRESS_TRACKER is not None:
                    PROGRESS_TRACKER.bump_network_fetch()
                return body
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in retryable_codes
            if retryable and (retry_forever or attempt < max_retries):
                attempt += 1
                if PROGRESS_TRACKER is not None:
                    PROGRESS_TRACKER.bump_retry(detail=f"{progress_label} retry in {backoff}s after HTTP {exc.code}")
                time.sleep(backoff)
                backoff = min(max_backoff, max(backoff + 1, int(backoff * 1.8)))
                continue
            raise FetchError(
                f"{url} returned HTTP {exc.code}: {body[:200]}",
                status_code=exc.code,
                retryable=retryable,
            ) from exc
        except URLError as exc:
            if retry_forever or attempt < max_retries:
                attempt += 1
                if PROGRESS_TRACKER is not None:
                    PROGRESS_TRACKER.bump_retry(detail=f"{progress_label} retry in {backoff}s after network error")
                time.sleep(backoff)
                backoff = min(max_backoff, max(backoff + 1, int(backoff * 1.8)))
                continue
            raise FetchError(f"{url} failed: {exc.reason}", retryable=True) from exc


def http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    cache_key: str | None = None,
    retryable_codes: set[int] | None = None,
    max_retries: int = 2,
    retry_forever: bool = False,
    initial_backoff: int = 5,
    max_backoff: int = 300,
    progress_label: str = "network request",
) -> Any:
    return json.loads(
        http_get_text(
            url,
            headers=headers,
            timeout=timeout,
            cache_key=cache_key,
            retryable_codes=retryable_codes,
            max_retries=max_retries,
            retry_forever=retry_forever,
            initial_backoff=initial_backoff,
            max_backoff=max_backoff,
            progress_label=progress_label,
        )
    )


def build_user_agent(mailto: str | None) -> str:
    if mailto:
        return f"latex-citation-curator/1.0 (mailto:{mailto})"
    return "latex-citation-curator/1.0"


def semantic_scholar_search(
    query: str,
    *,
    api_key: str,
    limit: int,
    user_agent: str,
    shared_initial_backoff: int,
    shared_max_backoff: int,
    shared_max_retries: int,
) -> tuple[list[dict[str, Any]], str]:
    search_query = build_search_query(query)
    params = urlencode({"query": search_query, "limit": str(limit), "fields": SEMANTIC_SCHOLAR_FIELDS})
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    cache_key_base = f"semantic_scholar:search:{normalize_title(search_query)}:limit={limit}"
    if api_key:
        try:
            payload = http_get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": user_agent,
                    "x-api-key": api_key,
                },
                cache_key=f"{cache_key_base}:auth",
                retryable_codes=RETRYABLE_HTTP_CODES,
                max_retries=2,
                progress_label="Semantic Scholar auth",
            )
            return list(payload.get("data", [])), "auth"
        except FetchError as exc:
            if exc.status_code not in {401, 403}:
                raise
            if PROGRESS_TRACKER is not None:
                PROGRESS_TRACKER.update(stage="Falling back to shared search", detail="Authenticated Semantic Scholar access was refused.", mode="shared")

    payload = http_get_json(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
        cache_key=f"{cache_key_base}:shared",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=shared_max_retries,
        retry_forever=shared_max_retries == 0,
        initial_backoff=shared_initial_backoff,
        max_backoff=shared_max_backoff,
        progress_label="Semantic Scholar shared",
    )
    return list(payload.get("data", [])), "shared"


def openalex_lookup_doi(doi: str, *, user_agent: str, mailto: str | None) -> dict[str, Any] | None:
    params: list[tuple[str, str]] = []
    if mailto:
        params.append(("mailto", mailto))
    query_suffix = f"?{urlencode(params)}" if params else ""
    normalized = normalize_doi(doi)
    url = f"https://api.openalex.org/works/https://doi.org/{quote(normalized, safe='')}{query_suffix}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"openalex:doi:{normalized.lower()}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="OpenAlex DOI lookup",
    )
    return payload if isinstance(payload, dict) else None


def openalex_search_query(query: str, *, limit: int, user_agent: str, mailto: str | None) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [
        ("search", build_search_query(query)),
        ("per-page", str(limit)),
        ("filter", "has_doi:true,is_retracted:false"),
    ]
    if mailto:
        params.append(("mailto", mailto))
    url = f"https://api.openalex.org/works?{urlencode(params)}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"openalex:search:{normalize_title(query)}:limit={limit}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="OpenAlex search",
    )
    return list(payload.get("results", []))


def openalex_search_title(
    title: str,
    *,
    limit: int,
    user_agent: str,
    mailto: str | None,
    require_doi: bool = False,
) -> list[dict[str, Any]]:
    filters = [f"title.search:{title}", "is_retracted:false"]
    if require_doi:
        filters.append("has_doi:true")
    params: list[tuple[str, str]] = [
        ("filter", ",".join(filters)),
        ("per-page", str(limit)),
    ]
    if mailto:
        params.append(("mailto", mailto))
    url = f"https://api.openalex.org/works?{urlencode(params)}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"openalex:title:{normalize_title(title)}:limit={limit}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="OpenAlex title search",
    )
    return list(payload.get("results", []))


def crossref_lookup_doi(doi: str, *, user_agent: str) -> dict[str, Any] | None:
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"crossref:doi:{doi.lower()}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="Crossref DOI lookup",
    )
    return payload.get("message")


def crossref_search_title(title: str, *, limit: int, user_agent: str, mailto: str | None) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [("query.title", title), ("rows", str(limit))]
    if mailto:
        params.append(("mailto", mailto))
    url = f"https://api.crossref.org/works?{urlencode(params)}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"crossref:title:{normalize_title(title)}:limit={limit}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="Crossref title search",
    )
    return list(payload.get("message", {}).get("items", []))


def doi_bibtex_lookup(doi: str, *, user_agent: str) -> str:
    url = f"https://doi.org/{quote(doi, safe='')}"
    return http_get_text(
        url,
        headers={
            "Accept": "application/x-bibtex; charset=utf-8",
            "User-Agent": user_agent,
        },
        cache_key=f"doi:bibtex:{doi.lower()}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="DOI BibTeX lookup",
    )


def dblp_search_title(title: str, *, limit: int, user_agent: str) -> list[dict[str, Any]]:
    params = urlencode({"q": title, "h": str(limit), "format": "json"})
    url = f"https://dblp.org/search/publ/api?{params}"
    payload = http_get_json(
        url,
        headers={"Accept": "application/json", "User-Agent": user_agent},
        cache_key=f"dblp:title:{normalize_title(title)}:limit={limit}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="DBLP title search",
    )
    hits = payload.get("result", {}).get("hits", {}).get("hit", [])
    if isinstance(hits, dict):
        return [hits]
    return list(hits)


def dblp_bibtex_url(record_url: str) -> str:
    url = normalize_whitespace(record_url)
    if not url:
        return ""
    return url if url.endswith(".bib") else url.rstrip("/") + ".bib"


def dblp_bibtex_lookup(record_url: str, *, user_agent: str) -> str:
    bibtex_url = dblp_bibtex_url(record_url)
    return http_get_text(
        bibtex_url,
        headers={
            "Accept": "application/x-bibtex; charset=utf-8",
            "User-Agent": user_agent,
        },
        cache_key=f"dblp:bibtex:{bibtex_url}",
        retryable_codes=RETRYABLE_HTTP_CODES,
        max_retries=3,
        progress_label="DBLP BibTeX lookup",
    )


def google_scholar_search_url(text: str) -> str:
    query = normalize_whitespace(text)
    if not query:
        return ""
    return "https://scholar.google.com/scholar?" + urlencode({"q": query})


def author_names(authors: list[dict[str, Any]]) -> list[str]:
    names = []
    for author in authors or []:
        name = str(author.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def infer_preprint(venue: str, url: str, publication_types: list[str], external_ids: dict[str, Any]) -> bool:
    haystack = " ".join([venue, url, " ".join(publication_types)]).casefold()
    if any(marker in haystack for marker in ("arxiv", "biorxiv", "medrxiv", "preprint")):
        return True
    return bool(external_ids.get("ArXiv"))


def semantic_scholar_record(raw: dict[str, Any], query: str) -> dict[str, Any]:
    external_ids = raw.get("externalIds") or {}
    publication_venue = raw.get("publicationVenue") or {}
    journal = raw.get("journal") or {}
    doi = str(external_ids.get("DOI", "") or "").strip()
    venue = (
        str(publication_venue.get("name", "")).strip()
        or str(journal.get("name", "")).strip()
        or str(raw.get("venue", "")).strip()
    )
    title = normalize_whitespace(str(raw.get("title", "")))
    abstract = normalize_whitespace(str(raw.get("abstract", "")))
    url = str(raw.get("url", "")).strip()
    publication_types = [str(item) for item in raw.get("publicationTypes") or []]
    preprint = infer_preprint(venue, url, publication_types, external_ids)
    peer_reviewed = not preprint
    return {
        "title": title,
        "abstract": abstract,
        "year": choose_year(raw.get("year")),
        "venue": venue,
        "doi": doi,
        "citationCount": choose_year(raw.get("citationCount")),
        "authors": author_names(raw.get("authors") or []),
        "url": url,
        "paperId": str(raw.get("paperId", "")).strip(),
        "publicationTypes": publication_types,
        "peerReviewed": peer_reviewed,
        "preprint": preprint,
        "relevanceScore": infer_relevance_score(query, title, abstract),
        "evidenceScore": infer_evidence_score(title, abstract, peer_reviewed),
        "semanticScholarUrl": url,
        "candidateSource": "semantic-scholar",
    }


def openalex_abstract(item: dict[str, Any]) -> str:
    inverted = item.get("abstract_inverted_index")
    if not isinstance(inverted, dict):
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                pairs.append((int(position), str(word)))
            except (TypeError, ValueError):
                continue
    if not pairs:
        return ""
    pairs.sort(key=lambda pair: pair[0])
    return normalize_whitespace(" ".join(word for _, word in pairs))


def openalex_authors(item: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    for authorship in item.get("authorships") or []:
        author = authorship.get("author") or {}
        name = normalize_whitespace(str(author.get("display_name", "")))
        if name:
            authors.append(name)
    return authors


def infer_openalex_preprint(item: dict[str, Any]) -> bool:
    type_crossref = str(item.get("type_crossref", "")).strip().casefold()
    if type_crossref in {"posted-content", "preprint"}:
        return True
    primary_location = item.get("primary_location") or {}
    version = str(primary_location.get("version", "")).strip().casefold()
    source = primary_location.get("source") or {}
    source_name = str(source.get("display_name", "")).strip().casefold()
    return version in {"acceptedversion", "submittedversion"} or "arxiv" in source_name


def openalex_venue(item: dict[str, Any]) -> str:
    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    venue = normalize_whitespace(str(source.get("display_name", "")))
    if venue:
        return venue
    biblio = item.get("biblio") or {}
    return normalize_whitespace(str(biblio.get("venue", "")))


def openalex_record(item: dict[str, Any], query_title: str) -> dict[str, Any]:
    title = normalize_whitespace(str(item.get("display_name", "")))
    abstract = openalex_abstract(item)
    doi = normalize_doi(str(item.get("doi", "")))
    url = normalize_whitespace(str(item.get("id", ""))) or (f"https://doi.org/{doi}" if doi else "")
    preprint = infer_openalex_preprint(item)
    peer_reviewed = not preprint
    return {
        "title": title,
        "abstract": abstract,
        "year": choose_year(item.get("publication_year")),
        "venue": openalex_venue(item),
        "doi": doi,
        "citationCount": choose_year(item.get("cited_by_count")),
        "authors": openalex_authors(item),
        "url": url,
        "type": normalize_whitespace(str(item.get("type_crossref", "") or item.get("type", ""))),
        "preprint": preprint,
        "peerReviewed": peer_reviewed,
        "openalexId": normalize_whitespace(str(item.get("id", ""))),
        "similarity": title_similarity(query_title, title),
        "candidateSource": "openalex",
    }


def choose_best_openalex(title: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [openalex_record(item, title) for item in items]
    candidates = [item for item in candidates if item["similarity"] >= 0.72]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            1 if not item["preprint"] else 0,
            item["similarity"],
            item["year"],
        ),
        reverse=True,
    )
    return candidates[0]


def crossref_year(item: dict[str, Any]) -> int:
    for field in ("published-print", "published-online", "issued", "created"):
        parts = item.get(field, {}).get("date-parts", [])
        if parts and parts[0]:
            return choose_year(parts[0][0])
    return 0


def crossref_title(item: dict[str, Any]) -> str:
    titles = item.get("title") or []
    if titles:
        return normalize_whitespace(str(titles[0]))
    return ""


def crossref_venue(item: dict[str, Any]) -> str:
    containers = item.get("container-title") or []
    if containers:
        return normalize_whitespace(str(containers[0]))
    event = item.get("event", {})
    if isinstance(event, dict):
        return normalize_whitespace(str(event.get("name", "")))
    return ""


def crossref_authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in item.get("author") or []:
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        name = normalize_whitespace(f"{given} {family}")
        if name:
            names.append(name)
    return names


def crossref_record(item: dict[str, Any], query_title: str) -> dict[str, Any]:
    title = crossref_title(item)
    abstract = normalize_whitespace(re.sub(r"<[^>]+>", " ", str(item.get("abstract", ""))))
    venue = crossref_venue(item)
    doi = str(item.get("DOI", "")).strip()
    url = str(item.get("URL", "")).strip() or (f"https://doi.org/{doi}" if doi else "")
    item_type = str(item.get("type", "")).strip()
    preprint = item_type in {"posted-content", "preprint"}
    peer_reviewed = not preprint
    return {
        "title": title,
        "abstract": abstract,
        "year": crossref_year(item),
        "venue": venue,
        "doi": doi,
        "authors": crossref_authors(item),
        "url": url,
        "type": item_type,
        "preprint": preprint,
        "peerReviewed": peer_reviewed,
        "similarity": title_similarity(query_title, title),
    }


def choose_best_crossref(title: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [crossref_record(item, title) for item in items]
    candidates = [item for item in candidates if item["similarity"] >= 0.72]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            1 if not item["preprint"] else 0,
            item["similarity"],
            item["year"],
        ),
        reverse=True,
    )
    return candidates[0]


def infer_dblp_preprint(venue: str, url: str, item_type: str) -> bool:
    haystack = " ".join([venue, url, item_type]).casefold()
    venue_norm = normalize_title(venue)
    item_type_norm = normalize_title(item_type)
    if venue_norm in {"corr", "arxiv"}:
        return True
    if item_type_norm == "informal and other publications":
        return True
    return any(marker in haystack for marker in ("arxiv", "preprint"))


def dblp_record(hit: dict[str, Any], title: str) -> dict[str, Any]:
    info = hit.get("info", {})
    item_title = normalize_whitespace(str(info.get("title", "")))
    authors = info.get("authors", {}).get("author", [])
    if isinstance(authors, dict):
        authors = [authors]
    if isinstance(authors, list):
        author_list = []
        for author in authors:
            if isinstance(author, dict):
                author_list.append(str(author.get("text", "")).strip())
            else:
                author_list.append(str(author).strip())
    else:
        author_list = []
    doi = str(info.get("doi", "")).strip()
    url = str(info.get("url", "")).strip()
    venue = normalize_whitespace(str(info.get("venue", "")))
    item_type = normalize_whitespace(str(info.get("type", "")))
    preprint = infer_dblp_preprint(venue, url, item_type)
    return {
        "title": item_title,
        "year": choose_year(info.get("year")),
        "venue": venue,
        "doi": doi,
        "authors": [name for name in author_list if name],
        "url": url,
        "bibtexUrl": dblp_bibtex_url(url),
        "type": item_type,
        "preprint": preprint,
        "peerReviewed": not preprint,
        "similarity": title_similarity(title, item_title),
    }


def choose_best_dblp(title: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [dblp_record(hit, title) for hit in hits]
    candidates = [item for item in candidates if item["similarity"] >= 0.72]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            1 if not item["preprint"] else 0,
            item["similarity"],
            item["year"],
        ),
        reverse=True,
    )
    return candidates[0]


def candidate_identity(candidate: dict[str, Any]) -> str:
    doi = normalize_doi(str(candidate.get("doi", "")))
    if doi:
        return f"doi:{doi.lower()}"
    return f"title:{normalize_title(str(candidate.get('title', '')))}"


def merge_candidates(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in primary + secondary:
        identity = candidate_identity(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(candidate)
    return merged


def candidate_passes_relevance_gate(candidate: dict[str, Any], *, min_relevance_score: float) -> bool:
    return float(candidate.get("relevanceScore", 0.0) or 0.0) >= min_relevance_score


def merge_missing_fields(base: dict[str, Any], overlay: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if not base.get(field) and overlay.get(field):
            base[field] = overlay[field]


def apply_venue_hints(record: dict[str, Any], venue_hints: dict[str, Any]) -> None:
    venue = normalize_title(str(record.get("venue", "")))
    if not venue:
        return
    hint = venue_hints.get(venue)
    if not isinstance(hint, dict):
        return
    for field in ("ccfTier", "jcrQuartile", "impactFactor"):
        if field in hint and field not in record:
            record[field] = hint[field]


def append_provenance_fields(bibtex: str, fields: dict[str, str]) -> str:
    entry = bibtex.strip()
    if not entry.endswith("}"):
        return entry
    additions = []
    for key, value in fields.items():
        if re.search(rf"(?im)^\s*{re.escape(key)}\s*=", entry):
            continue
        additions.append(f"  {key} = {{{value}}}")
    if not additions:
        return entry
    insertion = ",\n".join(additions)
    return entry[:-1].rstrip() + ",\n" + insertion + "\n}\n"


def bibtex_entry_key(entry: str) -> str:
    match = re.search(r"(?is)@\w+\s*\{\s*([^,\s]+)", entry)
    return normalize_whitespace(match.group(1)) if match else ""


def bibtex_field_value(entry: str, field: str) -> str:
    pattern = re.compile(rf"(?im)\b{re.escape(field)}\b\s*=")
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


def ledger_identity(record: dict[str, Any]) -> str:
    doi = normalize_doi(str(record.get("doi", "")))
    if doi:
        return f"doi:{doi.lower()}"
    key = normalize_whitespace(str(record.get("bibtexKey", "")))
    if key:
        return f"key:{key}"
    title = normalize_title(str(record.get("title", "")))
    if title:
        return f"title:{title}"
    return ""


def infer_local_checked_status(entry: dict[str, Any]) -> bool:
    verification_status = str(entry.get("verificationStatus", "")).strip()
    verified_at = str(entry.get("verifiedAt", "")).strip()
    return bool(
        verification_status in {"verified-doi", "trusted-bibtex-no-doi"}
        or verified_at
        or entry.get("manualCheckRequired")
    )


def merge_record_values(base: dict[str, Any], overlay: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if overlay.get(field) not in (None, "", [], {}):
            base[field] = overlay[field]


def parse_local_bib_entry(entry: str, bib_path: Path) -> dict[str, Any]:
    title = bibtex_field_value(entry, "title")
    doi = normalize_doi(bibtex_field_value(entry, "doi"))
    venue = (
        bibtex_field_value(entry, "journal")
        or bibtex_field_value(entry, "booktitle")
        or bibtex_field_value(entry, "series")
    )
    url = bibtex_field_value(entry, "url") or bibtex_field_value(entry, "ee")
    author_field = bibtex_field_value(entry, "author")
    authors = [normalize_whitespace(part) for part in re.split(r"\s+and\s+", author_field) if normalize_whitespace(part)]
    verification_sources = [
        normalize_whitespace(item)
        for item in bibtex_field_value(entry, "x-verified-with").split(",")
        if normalize_whitespace(item)
    ]
    source = bibtex_field_value(entry, "x-bib-source")
    source_url = bibtex_field_value(entry, "x-bib-source-url")
    verification_status = bibtex_field_value(entry, "x-verification-status")
    manual_check_required = bibtex_field_value(entry, "x-secondary-check-required").casefold() in {"1", "true", "yes"}
    quality_score_text = bibtex_field_value(entry, "x-quality-score")
    try:
        quality_score = float(quality_score_text) if quality_score_text else 0.0
    except ValueError:
        quality_score = 0.0
    item = {
        "bibtex": entry.strip() + "\n",
        "bibPath": str(bib_path.resolve()),
        "bibtexKey": bibtex_entry_key(entry),
        "title": title,
        "year": choose_year(bibtex_field_value(entry, "year")),
        "venue": venue,
        "doi": doi,
        "authors": authors,
        "url": url,
        "source": source,
        "sourceUrl": source_url,
        "verificationSources": verification_sources,
        "verifiedAt": bibtex_field_value(entry, "x-verified-at"),
        "verificationStatus": verification_status,
        "manualCheckRequired": manual_check_required,
        "manualCheckReasons": [],
        "qualityScore": quality_score,
        "reliableBibtexSource": source if source in TRUSTED_BIBTEX_SOURCES else "",
        "candidateSource": "local-bib",
    }
    preprint = infer_preprint(venue, url, [], {"ArXiv": bool(re.search(r"arxiv", url, flags=re.IGNORECASE))})
    item["preprint"] = preprint
    item["peerReviewed"] = not preprint
    item["checked"] = infer_local_checked_status(item)
    return item


def update_ledger_entry(
    ledger: dict[str, Any],
    record: dict[str, Any],
    *,
    bib_path: Path | None = None,
    checked_override: bool | None = None,
) -> None:
    identity = ledger_identity(record)
    if not identity:
        return
    entries = ledger.setdefault("entries", {})
    existing = entries.get(identity, {}) if isinstance(entries.get(identity), dict) else {}
    merged = dict(existing)
    merge_record_values(
        merged,
        record,
        [
            "bibtexKey",
            "title",
            "year",
            "venue",
            "doi",
            "url",
            "source",
            "sourceUrl",
            "verificationStatus",
            "verifiedAt",
            "qualityScore",
            "reliableBibtexSource",
            "googleScholarSearchUrl",
            "bibtex",
            "peerReviewed",
            "preprint",
        ],
    )
    if record.get("authors"):
        merged["authors"] = list(record.get("authors", []))
    if record.get("verificationSources"):
        merged["verificationSources"] = list(record.get("verificationSources", []))
    manual_reasons = record.get("manualCheckReasons") or []
    if manual_reasons:
        merged["manualCheckReasons"] = list(manual_reasons)
    merged["manualCheckRequired"] = bool(record.get("manualCheckRequired") or merged.get("manualCheckRequired"))
    merged["checked"] = bool(
        infer_local_checked_status(record)
        if checked_override is None
        else checked_override
    ) or bool(merged.get("checked"))
    merged["lastSeenAt"] = current_date_iso()
    bib_paths = set(str(path) for path in merged.get("bibPaths", []) if path)
    if bib_path is not None:
        bib_paths.add(str(bib_path.resolve()))
    merged["bibPaths"] = sorted(bib_paths)
    entries[identity] = merged


def update_user_library_entry(
    library: dict[str, Any],
    record: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> None:
    identity = ledger_identity(record)
    if not identity:
        return
    entries = library.setdefault("entries", {})
    existing = entries.get(identity, {}) if isinstance(entries.get(identity), dict) else {}
    merged = dict(existing)
    merge_record_values(
        merged,
        record,
        [
            "bibtexKey",
            "title",
            "year",
            "venue",
            "doi",
            "url",
            "source",
            "sourceUrl",
            "verificationStatus",
            "verifiedAt",
            "qualityScore",
            "reliableBibtexSource",
            "googleScholarSearchUrl",
            "bibtex",
            "peerReviewed",
            "preprint",
        ],
    )
    if record.get("authors"):
        merged["authors"] = list(record.get("authors", []))
    if record.get("verificationSources"):
        merged["verificationSources"] = list(record.get("verificationSources", []))
    if record.get("manualCheckReasons"):
        merged["manualCheckReasons"] = list(record.get("manualCheckReasons", []))
    merged["manualCheckRequired"] = bool(record.get("manualCheckRequired") or merged.get("manualCheckRequired"))
    merged["checked"] = bool(infer_local_checked_status(record) or merged.get("checked"))
    merged["lastUsedAt"] = current_date_iso()
    projects = set(str(path) for path in merged.get("projectRoots", []) if path)
    if project_root is not None:
        projects.add(str(project_root.resolve()))
    merged["projectRoots"] = sorted(projects)
    entries[identity] = merged


def sync_local_bib_entries(
    bib_paths: list[Path],
    ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    parsed_entries: list[dict[str, Any]] = []
    bib_files = ledger.setdefault("bibFiles", {})
    for bib_path in bib_paths:
        resolved = bib_path.resolve()
        if not resolved.exists():
            continue
        text = resolved.read_text(encoding="utf-8")
        file_entries = [parse_local_bib_entry(entry, resolved) for entry in split_bibtex_entries(text)]
        parsed_entries.extend(file_entries)
        bib_files[str(resolved)] = {
            "lastSyncedAt": current_date_iso(),
            "entryCount": len(file_entries),
        }
        for entry in file_entries:
            update_ledger_entry(ledger, entry, bib_path=resolved)

    merged_entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in parsed_entries:
        identity = ledger_identity(entry)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        ledger_entry = ledger.get("entries", {}).get(identity, {})
        merged = dict(entry)
        if isinstance(ledger_entry, dict):
            merge_record_values(
                merged,
                ledger_entry,
                [
                    "source",
                    "sourceUrl",
                    "verificationStatus",
                    "verifiedAt",
                    "qualityScore",
                    "reliableBibtexSource",
                    "googleScholarSearchUrl",
                    "bibtex",
                ],
            )
            if ledger_entry.get("verificationSources"):
                merged["verificationSources"] = list(ledger_entry.get("verificationSources", []))
            if ledger_entry.get("manualCheckReasons"):
                merged["manualCheckReasons"] = list(ledger_entry.get("manualCheckReasons", []))
            merged["manualCheckRequired"] = bool(
                merged.get("manualCheckRequired") or ledger_entry.get("manualCheckRequired")
            )
            merged["checked"] = bool(ledger_entry.get("checked") or merged.get("checked"))
        merged_entries.append(merged)
    return merged_entries


def ledger_search_entries(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for value in ledger.get("entries", {}).values():
        if not isinstance(value, dict):
            continue
        title = normalize_whitespace(str(value.get("title", "")))
        if not title:
            continue
        entry = dict(value)
        entry["title"] = title
        entry["year"] = choose_year(entry.get("year"))
        entry["candidateSource"] = "local-ledger"
        entry["peerReviewed"] = bool(entry.get("peerReviewed", not entry.get("preprint", False)))
        entry["preprint"] = bool(entry.get("preprint", False))
        entry["checked"] = bool(entry.get("checked") or infer_local_checked_status(entry))
        entry["verificationSources"] = list(entry.get("verificationSources", []))
        entry["manualCheckReasons"] = list(entry.get("manualCheckReasons", []))
        results.append(entry)
    return results


def user_library_search_entries(library: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for value in library.get("entries", {}).values():
        if not isinstance(value, dict):
            continue
        title = normalize_whitespace(str(value.get("title", "")))
        bibtex = str(value.get("bibtex", ""))
        if not title or not bibtex:
            continue
        entry = dict(value)
        entry["title"] = title
        entry["year"] = choose_year(entry.get("year"))
        entry["candidateSource"] = "user-library"
        entry["peerReviewed"] = bool(entry.get("peerReviewed", not entry.get("preprint", False)))
        entry["preprint"] = bool(entry.get("preprint", False))
        entry["checked"] = bool(entry.get("checked") or infer_local_checked_status(entry))
        entry["verificationSources"] = list(entry.get("verificationSources", []))
        entry["manualCheckReasons"] = list(entry.get("manualCheckReasons", []))
        results.append(entry)
    return results


def search_local_bib_candidates(
    query: str,
    local_entries: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for entry in local_entries:
        title = str(entry.get("title", ""))
        if not title:
            continue
        candidate = dict(entry)
        candidate["relevanceScore"] = infer_relevance_score(query, title, "")
        candidate["evidenceScore"] = infer_evidence_score(title, "", bool(candidate.get("peerReviewed", False)))
        if candidate["relevanceScore"] <= 1.0:
            continue
        ranked.append(candidate)
    ranked.sort(
        key=lambda item: (
            1 if item.get("checked") else 0,
            float(item.get("relevanceScore", 0.0) or 0.0),
            choose_year(item.get("year")),
        ),
        reverse=True,
    )
    if limit > 0:
        return ranked[:limit]
    return ranked


def build_scored_local_result(
    candidate: dict[str, Any],
    *,
    query: str,
    allow_preprint: bool,
    venue_hints: dict[str, Any],
) -> dict[str, Any]:
    result = dict(candidate)
    apply_venue_hints(result, venue_hints)
    result["relevanceScore"] = infer_relevance_score(query, str(result.get("title", "")), "")
    result["evidenceScore"] = infer_evidence_score(
        str(result.get("title", "")),
        "",
        bool(result.get("peerReviewed", False)),
    )
    if result.get("source") in TRUSTED_BIBTEX_SOURCES:
        result["reliableBibtexSource"] = result["source"]
    result["googleScholarSearchUrl"] = result.get("googleScholarSearchUrl") or google_scholar_search_url(
        str(result.get("title", "")) or query
    )
    scored = compute_scores(result, datetime.now(UTC).year, allow_preprint)
    scored["verificationSources"] = list(result.get("verificationSources", []))
    scored["notes"] = [f"reused-{result.get('candidateSource', 'local-cache')}-entry"]
    scored["manualCheckRequired"] = bool(result.get("manualCheckRequired"))
    scored["manualCheckReasons"] = list(result.get("manualCheckReasons", []))
    scored["googleScholarSearchUrl"] = str(result.get("googleScholarSearchUrl", ""))
    scored["verificationStatus"] = str(
        result.get("verificationStatus")
        or ("verified-doi" if scored.get("doi") else "trusted-bibtex-no-doi")
    )
    scored["bibtex"] = str(result.get("bibtex", ""))
    return scored


def replace_with_formal_version(
    result: dict[str, Any],
    formal_meta: dict[str, Any],
    *,
    source_name: str,
    notes: list[str],
) -> bool:
    if not result.get("preprint") or formal_meta.get("preprint"):
        return False
    result["hasFormalVersion"] = True
    result["preprint"] = False
    result["peerReviewed"] = True
    for field in ("title", "abstract", "year", "venue", "doi", "url"):
        if formal_meta.get(field):
            result[field] = formal_meta[field]
    if formal_meta.get("authors"):
        result["authors"] = formal_meta["authors"]
    if formal_meta.get("dblpUrl"):
        result["dblpUrl"] = formal_meta["dblpUrl"]
    if formal_meta.get("bibtexUrl"):
        result["bibtexUrl"] = formal_meta["bibtexUrl"]
    notes.append(f"preprint-replaced-with-{source_name}-formal-version")
    if not formal_meta.get("doi"):
        notes.append(f"formal-version-from-{source_name}-has-no-doi")
    return True


def enrich_with_verified_metadata(
    candidate: dict[str, Any],
    *,
    query: str,
    user_agent: str,
    crossref_limit: int,
    dblp_limit: int,
    openalex_limit: int,
    crossref_mailto: str | None,
    allow_preprint: bool,
    venue_hints: dict[str, Any],
) -> dict[str, Any]:
    result = dict(candidate)
    provenance_chain = [str(result.get("candidateSource", "semantic-scholar"))]
    notes: list[str] = []
    manual_check_reasons: list[str] = []
    crossref_meta: dict[str, Any] | None = None
    openalex_meta: dict[str, Any] | None = None
    dblp_meta: dict[str, Any] | None = None

    doi = str(result.get("doi", "")).strip()
    if doi:
        try:
            crossref_payload = crossref_lookup_doi(doi, user_agent=user_agent)
            if crossref_payload:
                crossref_meta = crossref_record(crossref_payload, result["title"])
                provenance_chain.append("crossref")
        except FetchError as exc:
            notes.append(f"crossref-doi-lookup-failed: {exc}")

        try:
            openalex_payload = openalex_lookup_doi(doi, user_agent=user_agent, mailto=crossref_mailto)
            if openalex_payload:
                openalex_meta = openalex_record(openalex_payload, result["title"])
                if "openalex" not in provenance_chain:
                    provenance_chain.append("openalex")
        except FetchError as exc:
            notes.append(f"openalex-doi-lookup-failed: {exc}")

    if not crossref_meta or crossref_meta["preprint"] or not crossref_meta.get("doi"):
        try:
            crossref_candidates = crossref_search_title(
                result["title"],
                limit=crossref_limit,
                user_agent=user_agent,
                mailto=crossref_mailto,
            )
            chosen = choose_best_crossref(result["title"], crossref_candidates)
            if chosen:
                if "crossref" not in provenance_chain:
                    provenance_chain.append("crossref")
                crossref_meta = chosen
        except FetchError as exc:
            notes.append(f"crossref-title-search-failed: {exc}")

    if openalex_limit > 0 and (not openalex_meta or result.get("preprint") or not openalex_meta.get("doi")):
        try:
            openalex_candidates = openalex_search_title(
                result["title"],
                limit=openalex_limit,
                user_agent=user_agent,
                mailto=crossref_mailto,
                require_doi=False,
            )
            chosen_openalex = choose_best_openalex(result["title"], openalex_candidates)
            if chosen_openalex:
                openalex_meta = chosen_openalex
                if "openalex" not in provenance_chain:
                    provenance_chain.append("openalex")
        except FetchError as exc:
            notes.append(f"openalex-title-search-failed: {exc}")

    if crossref_meta:
        if not replace_with_formal_version(result, crossref_meta, source_name="crossref", notes=notes):
            merge_missing_fields(result, crossref_meta, ["doi", "year", "venue", "abstract", "url"])
            if crossref_meta.get("authors"):
                result["authors"] = crossref_meta["authors"]
            if title_similarity(result["title"], crossref_meta["title"]) >= 0.8:
                result["title"] = crossref_meta["title"]

    if openalex_meta:
        if replace_with_formal_version(result, openalex_meta, source_name="openalex", notes=notes):
            pass
        merge_missing_fields(result, openalex_meta, ["doi", "year", "venue", "abstract", "url", "citationCount"])
        if openalex_meta.get("authors"):
            result["authors"] = result.get("authors") or openalex_meta["authors"]
        if title_similarity(result["title"], openalex_meta["title"]) >= 0.8:
            result["title"] = openalex_meta["title"]
        if openalex_meta.get("openalexId"):
            result["openalexId"] = openalex_meta["openalexId"]

    if dblp_limit > 0:
        try:
            dblp_candidates = dblp_search_title(result["title"], limit=dblp_limit, user_agent=user_agent)
            dblp_meta = choose_best_dblp(result["title"], dblp_candidates)
            if dblp_meta:
                if "dblp" not in provenance_chain:
                    provenance_chain.append("dblp")
                replace_with_formal_version(result, dblp_meta, source_name="dblp", notes=notes)
                merge_missing_fields(result, dblp_meta, ["year", "venue", "doi"])
                if dblp_meta.get("authors"):
                    result["authors"] = result.get("authors") or dblp_meta["authors"]
                if title_similarity(result["title"], dblp_meta["title"]) >= 0.8:
                    result["title"] = dblp_meta["title"]
                result["dblpUrl"] = dblp_meta.get("url", "")
                result["dblpBibtexUrl"] = dblp_meta.get("bibtexUrl", "")
        except FetchError as exc:
            notes.append(f"dblp-search-failed: {exc}")

    apply_venue_hints(result, venue_hints)

    result["relevanceScore"] = infer_relevance_score(query, result["title"], str(result.get("abstract", "")))
    result["evidenceScore"] = infer_evidence_score(
        result["title"],
        str(result.get("abstract", "")),
        bool(result.get("peerReviewed", False)),
    )

    bibtex = ""
    bibtex_source = ""
    bibtex_source_url = ""
    if result.get("doi"):
        try:
            bibtex = doi_bibtex_lookup(str(result["doi"]), user_agent=user_agent)
            bibtex_source = "doi-content-negotiation"
            bibtex_source_url = f"https://doi.org/{result['doi']}"
        except FetchError as exc:
            notes.append(f"doi-bibtex-fetch-failed: {exc}")

    if not bibtex and dblp_meta and not result.get("preprint") and dblp_meta.get("bibtexUrl"):
        try:
            bibtex = dblp_bibtex_lookup(str(dblp_meta["bibtexUrl"]), user_agent=user_agent)
            bibtex_source = "dblp"
            bibtex_source_url = str(dblp_meta["bibtexUrl"])
            if not result.get("doi"):
                manual_check_reasons.append("formal-version-has-no-doi; bibtex-downloaded-from-dblp")
                notes.append("manual-second-check-required")
        except FetchError as exc:
            notes.append(f"dblp-bibtex-fetch-failed: {exc}")

    if not bibtex_source:
        if result.get("doi"):
            bibtex_source = "doi-content-negotiation"
            bibtex_source_url = f"https://doi.org/{result['doi']}"
        elif dblp_meta and dblp_meta.get("bibtexUrl"):
            bibtex_source = "dblp"
            bibtex_source_url = str(dblp_meta["bibtexUrl"])
        else:
            bibtex_source = str(result.get("candidateSource", "semantic-scholar"))
            bibtex_source_url = str(result.get("semanticScholarUrl", "") or result.get("url", ""))

    result["source"] = bibtex_source
    result["sourceUrl"] = bibtex_source_url
    if bibtex_source in TRUSTED_BIBTEX_SOURCES:
        result["reliableBibtexSource"] = bibtex_source
    result["manualCheckRequired"] = bool(manual_check_reasons)
    result["manualCheckReasons"] = manual_check_reasons
    result["googleScholarSearchUrl"] = google_scholar_search_url(str(result.get("title", "")) or query)

    scored = compute_scores(result, datetime.now(UTC).year, allow_preprint)
    scored["verificationSources"] = provenance_chain
    scored["notes"] = notes
    scored["manualCheckRequired"] = bool(manual_check_reasons)
    scored["manualCheckReasons"] = manual_check_reasons
    scored["googleScholarSearchUrl"] = result["googleScholarSearchUrl"]
    scored["verificationStatus"] = (
        "verified-doi"
        if scored.get("doi")
        else "trusted-bibtex-no-doi"
        if bibtex and scored.get("reliableBibtexSource")
        else "unverified"
    )

    if bibtex:
        landing_url = (
            f"https://doi.org/{scored['doi']}"
            if scored.get("doi")
            else str(scored.get("dblpUrl", "") or scored.get("sourceUrl", ""))
        )
        provenance_fields = {
            "x-bib-source": scored["source"],
            "x-bib-source-url": scored["sourceUrl"],
            "x-verified-with": ",".join(scored["verificationSources"]),
            "x-verified-at": current_date_iso(),
            "x-quality-score": str(scored["qualityScore"]),
            "x-verification-status": str(scored["verificationStatus"]),
        }
        if landing_url:
            provenance_fields["url"] = landing_url
        if scored.get("manualCheckRequired"):
            provenance_fields["x-secondary-check-required"] = "true"
        bibtex = append_provenance_fields(
            bibtex,
            provenance_fields,
        )
    else:
        scored["eligible"] = False
        scored.setdefault("rejectionReasons", []).append("bibtex-download-failed")
    scored["bibtex"] = bibtex
    return scored


def load_venue_hints(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    venues = payload.get("venues", payload)
    if not isinstance(venues, dict):
        raise ValueError("Venue hints must be a JSON object or contain a top-level 'venues' object.")
    return {normalize_title(key): value for key, value in venues.items()}


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []
    if args.query:
        queries.extend(args.query)
    if args.claims_json:
        payload = json.loads(Path(args.claims_json).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("--claims-json must contain a JSON array.")
        for item in payload:
            if isinstance(item, dict):
                value = item.get("clean_claim") or item.get("claim") or item.get("text")
            else:
                value = item
            value = normalize_whitespace(str(value or ""))
            if value:
                queries.append(value)
    return queries


def resolve_existing_bib_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for raw_path in args.existing_bib or []:
        if raw_path:
            paths.append(Path(raw_path).expanduser())
    for candidate in (args.append_bib, args.write_bib):
        if candidate:
            path = Path(candidate).expanduser()
            if path.exists() and path.suffix.lower() == ".bib":
                paths.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(Path(resolved))
    return unique


def resolve_project_root(args: argparse.Namespace, bib_paths: list[Path]) -> Path:
    if args.project_root:
        return Path(args.project_root).expanduser().resolve()
    for path in bib_paths:
        return path.parent.resolve()
    for candidate in (args.claims_json, args.append_bib, args.write_bib):
        if candidate:
            return Path(candidate).expanduser().resolve().parent
    return Path.cwd().resolve()


def render_markdown(report: list[dict[str, Any]], top: int) -> str:
    lines: list[str] = []
    for item in report:
        lines.append(f"## Claim\n{item['query']}\n")
        if item.get("googleScholarSearchUrl"):
            lines.append(f"Scholar hint: {item['googleScholarSearchUrl']}\n")
        results = item["results"][:top] if top > 0 else item["results"]
        if not results:
            lines.append("No verified candidates are ready yet.\n")
            continue
        for index, result in enumerate(results, start=1):
            lines.append(
                f"{index}. {result['title']} ({result.get('year', 'unknown')})"
                f" | venue: {result.get('venue', 'unknown')}"
                f" | doi: {result.get('doi', 'missing')}"
                f" | status: {result.get('verificationStatus', 'unknown')}"
                f" | bib-source: {result.get('source', 'unknown')}"
                f" | score: {result.get('qualityScore', 'n/a')}"
            )
            if result.get("manualCheckRequired"):
                reason_text = "; ".join(str(reason) for reason in result.get("manualCheckReasons", [])) or "manual second check required"
                lines.append(f"   manual check: {reason_text}")
                if result.get("googleScholarSearchUrl"):
                    lines.append(f"   scholar hint: {result['googleScholarSearchUrl']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extract_dois(text: str) -> set[str]:
    pattern = re.compile(r"(?im)^\s*doi\s*=\s*[{\" ]([^}\",\n]+)")
    return {match.group(1).strip().lower() for match in pattern.finditer(text)}


def extract_bibtex_keys(text: str) -> set[str]:
    pattern = re.compile(r"(?im)@\w+\s*\{\s*([^,\s]+)")
    return {match.group(1).strip() for match in pattern.finditer(text)}


def append_bibtex_entries(path: str, entries: list[str]) -> tuple[int, int]:
    target = Path(path)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    existing_dois = extract_dois(existing)
    existing_keys = extract_bibtex_keys(existing)
    new_entries: list[str] = []
    skipped = 0
    for entry in entries:
        entry_dois = extract_dois(entry)
        entry_keys = extract_bibtex_keys(entry)
        if entry_dois and any(doi in existing_dois for doi in entry_dois):
            skipped += 1
            continue
        if not entry_dois and entry_keys and any(key in existing_keys for key in entry_keys):
            skipped += 1
            continue
        existing_dois.update(entry_dois)
        existing_keys.update(entry_keys)
        new_entries.append(entry.strip() + "\n")
    if new_entries:
        prefix = "\n" if existing and not existing.endswith("\n") else ""
        target.write_text(existing + prefix + "\n".join(new_entries), encoding="utf-8")
    return len(new_entries), skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        action="append",
        help="A claim or search query. Repeat for multiple claims.",
    )
    parser.add_argument(
        "--claims-json",
        help="Path to JSON output from extract_citation_needs.py or a plain array of claims.",
    )
    parser.add_argument(
        "--existing-bib",
        action="append",
        help="Existing BibTeX file to prioritize and sync with the project ledger. Repeat for multiple files.",
    )
    parser.add_argument(
        "--project-root",
        help="Project root used for the local .latex-citation-curator ledger directory. Defaults to the BibTeX file directory or cwd.",
    )
    parser.add_argument(
        "--semantic-scholar-api-key",
        default=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""),
        help="Semantic Scholar API key. Defaults to SEMANTIC_SCHOLAR_API_KEY.",
    )
    parser.add_argument(
        "--no-key-prompt",
        action="store_true",
        help="Do not prompt for a Semantic Scholar API key; continue with stored or shared mode only.",
    )
    parser.add_argument(
        "--crossref-mailto",
        default=os.environ.get("CROSSREF_MAILTO", ""),
        help="Contact email for Crossref polite pool requests.",
    )
    parser.add_argument(
        "--venue-hints",
        help="Optional JSON mapping from venue names to ccfTier/jcrQuartile/impactFactor.",
    )
    parser.add_argument(
        "--semantic-limit",
        type=int,
        default=8,
        help="Semantic Scholar candidates per query before verification.",
    )
    parser.add_argument(
        "--crossref-limit",
        type=int,
        default=5,
        help="Crossref title matches to inspect for each candidate.",
    )
    parser.add_argument(
        "--dblp-limit",
        type=int,
        default=5,
        help="DBLP search matches to inspect for each candidate.",
    )
    parser.add_argument(
        "--openalex-limit",
        type=int,
        default=5,
        help="OpenAlex search matches to inspect for each query or candidate title.",
    )
    parser.add_argument(
        "--local-bib-limit",
        type=int,
        default=5,
        help="Local BibTeX entries to consider per query before remote discovery.",
    )
    parser.add_argument(
        "--shared-initial-backoff",
        type=int,
        default=15,
        help="Initial backoff in seconds for shared Semantic Scholar retries.",
    )
    parser.add_argument(
        "--shared-max-backoff",
        type=int,
        default=300,
        help="Maximum backoff in seconds for shared Semantic Scholar retries.",
    )
    parser.add_argument(
        "--shared-max-retries",
        type=int,
        default=0,
        help="Retry count for shared Semantic Scholar retries. Use 0 to retry forever.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="Number of results to keep per claim. Use 0 to keep all.",
    )
    parser.add_argument(
        "--min-relevance-score",
        type=float,
        default=4.0,
        help="Minimum relevance score required for a candidate to be delivered.",
    )
    parser.add_argument(
        "--allow-preprint",
        action="store_true",
        help="Allow preprints when no formal version can be verified.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown", "bibtex"),
        default="markdown",
        help="Console output format.",
    )
    parser.add_argument(
        "--write-json",
        help="Optional path to write the structured verification report.",
    )
    parser.add_argument(
        "--write-bib",
        help="Optional path to write verified BibTeX entries.",
    )
    parser.add_argument(
        "--append-bib",
        help="Optional .bib file to append verified entries into, skipping duplicate DOIs.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable stderr progress boxes.",
    )
    return parser.parse_args()


def main() -> int:
    global NETWORK_CACHE
    global PROGRESS_TRACKER

    args = parse_args()
    queries = load_queries(args)
    if not queries:
        print("Provide at least one --query or --claims-json input.", file=sys.stderr)
        return 2
    effective_api_key, provided_api_key, key_source = resolve_semantic_scholar_key(args)
    existing_bib_paths = resolve_existing_bib_paths(args)
    project_root = resolve_project_root(args, existing_bib_paths)
    ledger_file = project_ledger_path(project_root)
    library_file = user_library_path()
    ledger = load_verification_ledger(ledger_file)
    user_library = load_user_library(library_file)
    local_bib_entries = sync_local_bib_entries(existing_bib_paths, ledger)
    for entry in local_bib_entries:
        update_user_library_entry(user_library, entry, project_root=project_root)
    local_reference_entries = merge_candidates(local_bib_entries, ledger_search_entries(ledger))
    local_reference_entries = merge_candidates(local_reference_entries, user_library_search_entries(user_library))
    save_verification_ledger(ledger_file, ledger)
    save_user_library(library_file, user_library)

    venue_hints = load_venue_hints(args.venue_hints)
    user_agent = build_user_agent(args.crossref_mailto or None)
    NETWORK_CACHE = PersistentCache(cache_store_path())
    PROGRESS_TRACKER = ProgressTracker(total_queries=len(queries), enabled=not args.no_progress)
    report: list[dict[str, Any]] = []
    bibtex_entries: list[str] = []
    stored_path: Path | None = None

    for query_index, query in enumerate(queries, start=1):
        local_candidates = search_local_bib_candidates(query, local_reference_entries, limit=args.local_bib_limit)
        PROGRESS_TRACKER.update(
            current_query=query_index,
            stage="Checking local BibTeX and user library",
            detail=f"Matched {len(local_candidates)} local candidates.",
            mode="local-ledger-library",
        )
        PROGRESS_TRACKER.update(
            current_query=query_index,
            stage="Searching candidates",
            detail=truncate_box_text(query, 56),
            mode="auth" if effective_api_key else key_source,
        )
        raw_candidates, search_mode = semantic_scholar_search(
            query,
            api_key=effective_api_key,
            limit=args.semantic_limit,
            user_agent=user_agent,
            shared_initial_backoff=args.shared_initial_backoff,
            shared_max_backoff=args.shared_max_backoff,
            shared_max_retries=args.shared_max_retries,
        )
        semantic_candidates = [semantic_scholar_record(raw_candidate, query) for raw_candidate in raw_candidates]
        PROGRESS_TRACKER.update(
            current_query=query_index,
            stage="Supplementing with OpenAlex",
            detail=f"Semantic Scholar returned {len(semantic_candidates)} candidates.",
            mode=search_mode,
        )
        if args.openalex_limit > 0:
            try:
                openalex_raw_candidates = openalex_search_query(
                    query,
                    limit=args.openalex_limit,
                    user_agent=user_agent,
                    mailto=args.crossref_mailto or None,
                )
                openalex_candidates = [
                    candidate
                    for candidate in (openalex_record(item, query) for item in openalex_raw_candidates)
                    if candidate_passes_relevance_gate(candidate, min_relevance_score=args.min_relevance_score)
                ]
            except FetchError as exc:
                openalex_candidates = []
                if PROGRESS_TRACKER is not None:
                    PROGRESS_TRACKER.update(
                        current_query=query_index,
                        stage="OpenAlex supplement failed",
                        detail=truncate_box_text(str(exc), 56),
                        mode=search_mode,
                    )
        else:
            openalex_candidates = []
        candidates = merge_candidates(semantic_candidates, openalex_candidates)
        candidates = merge_candidates(local_candidates, candidates)
        PROGRESS_TRACKER.update(
            current_query=query_index,
            stage="Candidate search complete",
            detail=f"Retrieved {len(candidates)} merged candidates.",
            mode=f"local+{search_mode}+openalex",
        )
        if provided_api_key and search_mode == "auth" and not stored_path:
            try:
                stored_path = save_stored_api_key(provided_api_key)
            except OSError as exc:
                print(f"Failed to store Semantic Scholar API key: {exc}", file=sys.stderr)
        verified_results = []
        for candidate_index, candidate in enumerate(candidates, start=1):
            PROGRESS_TRACKER.update(
                current_query=query_index,
                stage=f"Verifying candidate {candidate_index}/{len(candidates)}",
                detail=truncate_box_text(str(candidate.get("title", "")), 56),
                mode=f"local+{search_mode}+openalex",
            )
            try:
                if (
                    candidate.get("candidateSource") in LOCAL_REUSE_SOURCES
                    and candidate.get("checked")
                    and candidate.get("bibtex")
                ):
                    verified = build_scored_local_result(
                        candidate,
                        query=query,
                        allow_preprint=args.allow_preprint,
                        venue_hints=venue_hints,
                    )
                else:
                    verified = enrich_with_verified_metadata(
                        candidate,
                        query=query,
                        user_agent=user_agent,
                        crossref_limit=args.crossref_limit,
                        dblp_limit=args.dblp_limit,
                        openalex_limit=args.openalex_limit,
                        crossref_mailto=args.crossref_mailto or None,
                        allow_preprint=args.allow_preprint,
                        venue_hints=venue_hints,
                    )
                verified_results.append(verified)
            except FetchError as exc:
                if PROGRESS_TRACKER is not None:
                    PROGRESS_TRACKER.update(
                        current_query=query_index,
                        stage="Candidate skipped internally",
                        detail=truncate_box_text(str(exc), 56),
                        mode=f"local+{search_mode}+openalex",
                    )

        verified_results.sort(
            key=lambda item: (
                0 if item["eligible"] else 1,
                -float(item["qualityScore"]),
                -choose_year(item.get("year")),
            )
        )
        verified_results = [
            item
            for item in verified_results
            if item.get("eligible")
            and item.get("bibtex")
            and candidate_passes_relevance_gate(item, min_relevance_score=args.min_relevance_score)
        ]
        if args.top > 0:
            verified_results = verified_results[: args.top]

        for item in verified_results:
            bibtex_entries.append(item["bibtex"])
            ledger_record = dict(item)
            ledger_record["bibtexKey"] = bibtex_entry_key(str(item.get("bibtex", "")))
            update_ledger_entry(ledger, ledger_record)
            update_user_library_entry(user_library, ledger_record, project_root=project_root)

        report.append(
            {
                "query": query,
                "googleScholarSearchUrl": google_scholar_search_url(query),
                "results": verified_results,
            }
        )
        PROGRESS_TRACKER.update(
            current_query=query_index,
            stage="Query complete",
            detail=f"Prepared {len(verified_results)} verified entries.",
            mode=f"local+{search_mode}+openalex",
        )

    if args.write_json:
        Path(args.write_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.write_bib:
        Path(args.write_bib).write_text("\n".join(entry.strip() for entry in bibtex_entries if entry.strip()) + "\n", encoding="utf-8")
    if args.append_bib:
        written, skipped = append_bibtex_entries(args.append_bib, bibtex_entries)
        print(f"Appended {written} BibTeX entries; skipped {skipped} duplicates.", file=sys.stderr)

    sync_targets = resolve_existing_bib_paths(args)
    if sync_targets:
        local_bib_entries = sync_local_bib_entries(sync_targets, ledger)
        for entry in local_bib_entries:
            update_user_library_entry(user_library, entry, project_root=project_root)
        local_reference_entries = merge_candidates(local_bib_entries, ledger_search_entries(ledger))
        local_reference_entries = merge_candidates(local_reference_entries, user_library_search_entries(user_library))
    save_verification_ledger(ledger_file, ledger)
    save_user_library(library_file, user_library)

    if args.format == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif args.format == "bibtex":
        sys.stdout.write("\n".join(entry.strip() for entry in bibtex_entries if entry.strip()) + "\n")
    else:
        sys.stdout.write(render_markdown(report, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
