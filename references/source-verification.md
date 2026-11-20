# Source Verification

## Contents

- Acceptance conditions
- Field checks and source observations
- DOI and bibliography checks
- Versions and dates
- Stored evidence and local edits
- Output and provenance
- Claim support

## Acceptance conditions

`scripts/verification.py` is the shared implementation used by the fetcher and scorer.
Verification concerns bibliographic metadata only. It is not proof of paper quality,
peer review, or support for a manuscript claim.

| State | Meaning |
|---|---|
| `verified-doi` | All required fields agree, formal publication is established, DOI returned matching bibliography, output BibTeX agrees |
| `verified-metadata-no-doi` | Required fields and trusted bibliography agree; no DOI found in observed records, which does not prove that none exists |
| `pending-verification` | Required value or sufficient corroboration missing |
| `metadata-conflict` | Required fields have unresolved differing values |
| `verification-failed` | Required check failed, for example invalid DOI, unestablished formal publication, or mismatching BibTeX |

Only the first two states are passing statuses. `trusted-bibtex-no-doi` is a
legacy/source description, not a passing status. Old labels, `checked`, dates,
source names, and manual-check flags do not prove verification. Only passing
records enter `results` and verified `.bib` output. Other attempted records
remain in `reviewRequired`, the ledger, and the user library.

## Field checks and source observations

Preserve each source's actual values before merging records in `metadataObservations`:

```json
{
  "source": "crossref",
  "sourceUrl": "URL of the observed record",
  "values": {
    "title": "Observed title",
    "authors": ["First Author", "Second Author"],
    "year": 2025,
    "venue": "Observed venue",
    "publicationStatus": "formal",
    "doi": "DOI actually returned by source",
    "volume": "",
    "issue": "",
    "pages": "",
    "articleNumber": ""
  }
}
```

`fieldChecks` stores the selected value, original values with URLs, status and
reason for every core and detail field. Statuses: `passed`, `missing`,
`insufficient-evidence`, `conflict`, `failed`, `not-reported`.
`unresolvedFields` lists required fields that did not pass.

Required: title, complete ordered authors, year, venue, publication status and
bibliography. DOI is required when present. Volume, issue, pages and article
number become required if any source or the output supplies them. Details
absent everywhere are `not-reported`, not verified; this does not prove they
are absent from the actual publication.

A field passes only if at least two distinct metadata source families with URLs
agree, and the output agrees. Implemented families: Semantic Scholar, OpenAlex,
DBLP, CNKI, registration/publisher. Crossref and DOI content negotiation count
as one family; CNKI and its export count as one family. Repeated requests to
one service do not provide independent corroboration. Databases can share
upstream errors: these are defined cross-source checks, not a guarantee.

Normalize case, punctuation, basic LaTeX formatting/accents, author `Family,
Given` order, DBLP author number suffixes and page dashes. Do not silently
equate initials with full names, venue acronyms with full names, different
author orders or different years. These remain explicit conflicts pending review.

Formal status requires an explicit publication type and venue plus corroboration
from Crossref, publisher metadata, DBLP or CNKI. Merely not containing "arXiv",
or being declared `@article` in BibTeX, is insufficient.

Retain every differing source value. Only a documented corrected source or
established version relationship can supersede a record; preserve the original
with an explanation. Never drop an inconvenient source to obtain a pass.

## DOI and bibliography checks

For DOI-bearing records:

1. Check DOI syntax and actual metadata from Crossref and other sources.
2. Request bibliography through DOI content negotiation.
3. Confirm the returned bibliography contains the requested DOI.
4. Compare its title, ordered authors, year, venue and supplied details.
5. Compare the actual output BibTeX against selected metadata too.

`doiResolution` records requested/returned DOI, source URL and match result.
A DOI string, HTTP success or search hit alone is insufficient. Missing export,
missing/wrong DOI in the export, or differing paper fields cannot pass.

`bibtexMetadata` contains parsed output fields. An actual trusted export also
becomes an observation with `role: "bibliography"`. Supported export providers:
DOI content negotiation, DBLP, CNKI export. A provider label without the actual
export is insufficient. No-DOI papers require the same field checks and trusted
export; one CNKI record plus its own export remains only one family.

CNKI remains browser assisted. The fetcher does not automate CNKI or directly
ingest exports. For externally curated CNKI records, preserve observations,
export content and parsed BibTeX using this schema and run `score_papers.py`
through the shared checker. Do not set passing statuses manually. See
[cnki-workflow.md](cnki-workflow.md).

## Versions and dates

Automatic preprint replacement requires matching normalized title and complete
ordered authors plus an explicit formal record. Approximate title similarity
alone is insufficient. Changed titles/authors require a verified relationship
and manual review. Keep the superseded preprint as `preprintBreadcrumb` outside
the formal edition's checks; compare the formal edition with its own sources.

Retain online/print/issued dates. Never use Crossref creation year as publication
year or automatically accept a one-year difference. Year discrepancies remain
conflicts with dates available for review. Explicitly allowed fallback preprints
stay outside verified bibliography; `--allow-preprint` cannot waive formal checks.

## Stored evidence and local edits

Persist schema version 2, source observations, DOI resolution, parsed BibTeX,
field checks, unresolved fields and fetch errors in the project
`.latex-citation-curator/verification-ledger.json` and user-cache `paper-library.json`.

Every reuse reruns checks against stored observations and current BibTeX.
This reuses previously observed evidence rather than making a fresh network
check. Legacy labels without observations remain pending and need rechecking.

During `.bib` synchronization, compare current user content to stored evidence.
Edits can invalidate the old result. Preserve user BibTeX, never replace it
with a cached copy. A failed recheck replaces the success state in both stores;
`checked` cannot remain true. Previous dates may remain as history, not current
success dates. Retain field results for failed candidates as well as accepted ones.

## Output and provenance

JSON contains `results` (passing metadata and relevance filter), `reviewRequired`
(attempted records whose checks did not pass), and full per-candidate checks,
observations, unresolved fields, fetch errors, status and claim-support state.
Markdown lists unresolved fields with source values. Timeouts are fetch errors,
not evidence of a metadata disagreement.

Generated BibTeX retains actual DOI when available and provenance fields:

```bibtex
x-bib-source = {actual-provider}
x-bib-source-url = {actual-export-url}
x-verified-with = {actual-sources-used}
x-verified-at = {successful-check-date}
x-verification-schema = {2}
x-verification-scope = {bibliographic-metadata}
x-verification-status = {derived-passing-status}
x-claim-support-status = {not-reviewed}
```

Detailed checks belong in JSON/state. Preserve user-edited entries.
`--existing-bib` reads; `--append-bib` preserves and appends accepted new entries;
`--write-bib` writes/replaces its target, so choose a new output unless replacement
was requested.

## Claim support

Relevance/evidence scores are text heuristics, not content checks. The fetcher
sets `claimSupport.status = "not-reviewed"` for every query, including reuse.
Metadata passing or a high score cannot establish a supporting citation.

Before recommending a paper as support, read the relevant method, theorem,
result or discussion. Record the claim, page/section/table/figure, source URL,
what is supported, setting/metric limitations and reviewer identity. Report
`supported`, `partially-supported`, `unsupported` or `not-reviewed` separately
from metadata status. The fetcher does not perform this reading or treat a
manual content verdict as metadata evidence.
