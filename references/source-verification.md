# Source Verification

## Table of Contents

- Required sources
- Minimum metadata
- Semantic Scholar access modes
- CNKI verification role
- Verification checklist
- Project ledger
- User-level paper library
- Preprint replacement
- BibTeX provenance fields
- Output checklist

## Required Sources

Prefer this source order:

0. Existing project `.bib` files and `.latex-citation-curator/verification-ledger.json`
0.5. The user-level `paper-library.json` cache
1. Semantic Scholar API
2. OpenAlex
3. Crossref, DOI resolver, or publisher landing page
4. DBLP when the topic is in computer science
5. CNKI when the claim needs Chinese-language scholarship, Chinese journals, Chinese abstracts or keywords, CSSCI/CSCD/北大核心 filtering, or GB/T 7714/EndNote metadata
6. Google Scholar only as a manual hint source for title variants or publisher breadcrumbs

Use at least two independent metadata sources for every final citation when possible.

OpenAlex is useful for DOI-bearing work discovery, citation counts, source normalization, and title-based fallback matching when Semantic Scholar is sparse or rate-limited.

CNKI is useful for Chinese-language candidate discovery, Chinese journal metadata, citation/download counts exposed by CNKI, source-category filters such as SCI/EI/CSSCI/CSCD/北大核心, and GB/T 7714 or EndNote exports. Treat CNKI as a metadata and provenance source, not as a substitute for DOI verification when a DOI exists.

Before remote discovery, inspect local `.bib` files, the project ledger, and the user-level paper library. If a matching entry was already checked and the record is still coherent, prefer reusing it.

## Minimum Metadata

Do not finalize a citation until you have all of these:

- title
- authors
- year
- venue
- DOI, or a trusted BibTeX/export provider when the formal version has no DOI
- source URL
- bibliography source URL or provider name

For CNKI-only formal publications, also capture the CNKI detail URL when available, the Chinese source/journal name, publication info, keywords or abstract if relevant to claim fit, and the exact export source such as CNKI GB/T 7714, EndNote, or RIS.

If one source is missing a field, reconcile it with another primary metadata source.

## Semantic Scholar Access Modes

Ask the user whether a Semantic Scholar API key is available.

If the user has a key:

- use authenticated Semantic Scholar requests when available
- persist the key after the first successful authenticated run
- record Semantic Scholar in the verification provenance when it actually participated

If the user does not have a key:

- continue with the free shared Semantic Scholar pool for discovery
- keep retrying shared-pool rate limits with long backoff
- verify accepted papers with OpenAlex, Crossref, DOI resolution, DBLP, and CNKI when applicable
- do not claim authenticated Semantic Scholar verification for those results

After the first successful authenticated run with a user-provided key, store the key in local secret storage and reuse it on later runs. Prefer `$CODEX_HOME/secrets/latex-citation-curator.json`, but if `$CODEX_HOME` lives on a mounted Windows path such as `/mnt/c/...`, use `~/.codex-secrets/latex-citation-curator.json` so the file can keep private permissions. Do not store keys inside the workspace or append them to generated output.

If authenticated Semantic Scholar access is refused, the script may fall back to the shared unauthenticated pool for candidate discovery. Keep retrying shared-pool rate limits with long backoff, and never expose failed intermediate attempts in the final BibTeX delivery.

## CNKI Verification Role

Use CNKI when the user asks for Chinese literature, CNKI papers, CSSCI, CSCD, 北大核心, Chinese policy/social-science evidence, Chinese empirical studies, or when English discovery sources underrepresent the topic.

CNKI may verify that a Chinese-language record exists in a formal source and may provide title, authors, journal/source, publication date, abstract, keywords, fund, classification, cited count, download count, and export citation formats. It does not by itself prove DOI metadata. If a DOI exists, still verify the DOI through Crossref, DOI resolution, publisher metadata, or another DOI-aware source.

For CNKI records without DOI:

- mark `x-verification-status = {trusted-bibtex-no-doi}` or an equivalent trusted-source status
- set `x-secondary-check-required = {true}` unless a second independent source confirms the same formal publication metadata
- record `cnki` in `x-verified-with`
- use `x-bib-source = {cnki-export}` when CNKI GB/T 7714, EndNote, RIS, or another CNKI export supplied the bibliography data

Respect CNKI access controls, institutional access requirements, and any visible captcha. If a visible CNKI/Tencent captcha appears, pause and ask the user to complete it manually.

## Verification Checklist

For every accepted citation:

1. Confirm the title matches across sources after normal punctuation normalization.
2. Confirm the publication year is consistent or explain the discrepancy.
3. Confirm at least the lead authors align across sources.
4. If a DOI exists, confirm the DOI resolves to the same paper.
5. If no DOI exists, confirm the record is a formal publication and get bibliography metadata from a trusted provider such as DBLP, CNKI export, publisher metadata, or another reliable source.
6. Confirm the venue is the final publication venue, not only a preprint server.
7. Confirm the BibTeX or citation metadata came from a real provider such as DBLP, DOI content negotiation, CNKI export, Crossref, or publisher metadata.
8. Mark no-DOI formal publications as requiring a manual second check unless two independent trusted sources align.
9. For CNKI source-category labels, verify that SCI, EI, CSSCI, CSCD, or 北大核心 was directly observed in CNKI or another source; do not infer it.

If any step fails, do not silently keep the record.

## Project Ledger

Maintain a project-local hidden directory named `.latex-citation-curator/`.

It should contain at least:

- `verification-ledger.json`: verification state keyed by DOI, BibTeX key, CNKI export ID, or normalized title

Use the ledger to track:

- whether the entry was checked
- whether it is `verified-doi` or `trusted-bibtex-no-doi`
- whether manual second checking is required
- which `.bib` files the entry came from or was synced into
- which sources verified it, including `cnki` when used
- when it was last seen and last updated

When the project already has a `.bib` file:

1. sync the ledger from the `.bib` file before remote discovery
2. reuse checked local entries before re-querying remote sources
3. sync accepted results back to the ledger after processing
4. if a `.bib` target was written or appended, sync the ledger from that `.bib` file again

## User-Level Paper Library

Maintain a user-level persistent paper library in the cache directory, typically `~/.cache/latex-citation-curator/paper-library.json`.

Use it to store:

- previously downloaded BibTeX or export-derived entries
- DOI, CNKI export ID, and normalized-title metadata
- verification status and provenance fields
- manual-check requirements
- project roots that reused the paper

When a paper is successfully processed, sync it into the user-level paper library so future projects can reuse it before hitting remote sources.

## Preprint Replacement

Treat preprints as provisional.

When a candidate looks like an arXiv, bioRxiv, or similar preprint:

1. Search by title in Semantic Scholar.
2. Search by title and authors in DBLP if the topic is computer science.
3. Search the DOI resolver or Crossref for the final venue record.
4. Search CNKI only when the topic or authorship plausibly has a Chinese journal/proceedings version.
5. If a formal publication exists, cite that version and discard the preprint from the final bibliography.
6. If the formal publication has no DOI, fetch BibTeX or export metadata from DBLP, CNKI export, or another trusted provider and mark it for manual second checking.

Use the preprint only when both are true:

- no formal published version can be verified
- the user explicitly allows fallback preprints

Even then, keep the item outside the final verified bibliography unless the user explicitly accepts the limitation.

## BibTeX Provenance Fields

Prefer a DOI in the BibTeX entry. When no DOI exists for the formal version, require a trusted BibTeX or citation export provider and add explicit provenance fields plus a manual-check marker.

Recommended custom fields:

```bibtex
x-bib-source = {cnki-export}
x-bib-source-url = {https://kns.cnki.net/...}
x-verified-with = {cnki,crossref}
x-verified-at = {2026-03-05}
x-quality-score = {82.4}
x-verification-status = {trusted-bibtex-no-doi}
x-secondary-check-required = {true}
```

Allowed provenance sources include:

- `dblp`
- `doi-content-negotiation`
- `crossref`
- `publisher-landing-page`
- `cnki`
- `cnki-export`
- `openalex`
- `semantic-scholar`

If the BibTeX was downloaded from DBLP and the DOI was confirmed via Crossref, record both in the verification fields.

If the citation metadata came from CNKI export and the DOI was later confirmed via Crossref or DOI resolution, keep `cnki-export` as the bibliography source and record the DOI-aware verifier in `x-verified-with`.

The default helper workflow downloads BibTeX from DOI content negotiation and records DBLP and Crossref as verification sources when used. CNKI integration is currently an instruction-level browser-assisted workflow; consult [cnki-workflow.md](cnki-workflow.md) when CNKI is needed.

## Output Checklist

Before returning the final answer, ensure that every accepted citation includes:

- DOI or an explicit note that no DOI was found for the formal version
- venue
- year
- explanation of claim fit
- provenance fields in BibTeX
- note of any uncertainty or exception
- a manual second-check note when the final BibTeX came from a trusted provider without a DOI
- for CNKI-derived entries, the CNKI source URL or export provider and any verified source-category label such as CSSCI, CSCD, SCI, EI, or 北大核心

If the task modifies a `.bib` file, keep existing entries intact and append only verified new entries.
