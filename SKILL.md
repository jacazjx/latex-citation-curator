---
name: latex-citation-curator
description: Find, verify, rank, and generate DOI-verified or trusted-source BibTeX citations for LaTeX manuscripts and research-writing workflows. Use when Codex needs to scan `.tex` files or draft prose for citation gaps such as `[cite]`, `[citation needed]`, `\todo{cite}`, or Chinese prompts like `我想找一篇论文来支撑论点`; search for real supporting papers; prefer the last 5 years, CCF A/B venues, JCR Q1/Q2 journals, high impact factor, and strong citation performance; replace preprints with formally published versions; prefer Semantic Scholar authenticated access when the user has a key, but fall back to the free shared flow when they do not; use Google Scholar only as a manual hint fallback; and emit BibTeX with explicit provenance fields.
---

# LaTeX Citation Curator

## Overview

Find citation gaps in LaTeX drafts, retrieve real candidate papers, rank them with a repeatable quality formula, and produce provenance-aware BibTeX entries.

Use this skill to support claims, not to pad a bibliography. Prefer fewer well-matched papers over many weak ones.

Create and maintain a project-local hidden state directory at `.latex-citation-curator/`. Use it to track whether BibTeX entries have already been checked, what verification status they reached, and which `.bib` files they were synced from.

Also maintain a user-level persistent library under the cache directory, such as `~/.cache/latex-citation-curator/paper-library.json`, so previously downloaded BibTeX and verified metadata can be reused across projects in the same research area.

## Workflow

### 1. Confirm the required inputs

Require these inputs before claiming a citation is verified:

- The LaTeX file, excerpt, or paragraph that needs support
- The target research area or venue expectations if the topic is narrow
- Ask whether the user has a Semantic Scholar API key available
- Ask whether the project already has one or more `.bib` files that should be treated as local ground truth

If the user has a key:

- use authenticated Semantic Scholar mode
- persist the key after the first successful authenticated run
- record that Semantic Scholar participated in verification

If the user does not have a key:

- continue with the free shared Semantic Scholar pool
- keep long backoff and retryable shared-mode retries enabled
- verify accepted papers with OpenAlex, Crossref, DOI resolution, and DBLP when applicable
- do not claim Semantic Scholar authenticated verification happened when it did not

Do not block the workflow solely because the user lacks a key.

When an authenticated Semantic Scholar request is refused, fall back to the unauthenticated shared channel for candidate discovery. Treat shared-channel results as input to further verification, not as verification by themselves.

When the user supplies a Semantic Scholar API key for the first successful run:

- store it in local secret storage
- default to `$CODEX_HOME/secrets/latex-citation-curator.json`
- if `$CODEX_HOME` is on a mounted Windows path such as `/mnt/c/...`, use `~/.codex-secrets/latex-citation-curator.json` instead so file permissions can be private
- reuse it automatically on future runs
- never write it into the repo, `.tex`, `.bib`, or report files

### 2. Locate citation gaps

Inspect the draft for markers such as:

- `[cite]`
- `[citation needed]`
- `\todo{cite}`
- plain-language notes such as `我想找一篇论文来支撑论点`, `需要引用`, `找文献支持`

Use `scripts/extract_citation_needs.py` when a local `.tex` file is available. The script extracts paragraph-level citation gaps with line numbers and cleaned claim text.

### 3. Rewrite each gap into a search target

For each claim:

- Reduce the paragraph to one verifiable statement
- Extract 3 to 8 search terms: task, method, population, metric, and constraint
- Generate English keywords even if the draft is in Chinese
- Keep one short note describing what kind of evidence is needed: benchmark result, survey, causal claim, system design, theory, or dataset paper

Prefer direct support over topical similarity.

### 4. Gather real candidates from primary metadata sources

Use this source order:

0. Existing project `.bib` files and the project-local `.latex-citation-curator/verification-ledger.json`
0.5. The user-level persistent paper library in the cache directory
1. Semantic Scholar API and OpenAlex for candidate discovery and metadata cross-checking
2. Crossref and DOI resolver or publisher landing pages for final DOI confirmation and official metadata
3. DBLP for computer-science bibliographic records
4. Google Scholar only as a manual hint source when DOI discovery fails or metadata is sparse

Before remote discovery, scan the local `.bib` files, the project ledger, and the user-level paper library. If a matching entry was already checked, reuse it first instead of re-fetching metadata.

If the shared Semantic Scholar pool returns rate limits:

- use long backoff
- keep retrying retryable failures instead of abandoning the query
- keep progress on stderr only
- do not surface failed intermediate attempts in the final user-facing output

Always collect:

- title
- authors
- year
- venue
- DOI when available
- abstract or summary
- citation count
- source URL
- BibTeX source URL if available

Read [references/source-verification.md](references/source-verification.md) when you need the exact verification checklist or BibTeX provenance rules.

### 5. Replace preprints with the final published version

Treat arXiv or other preprints as discovery hints, not final citations.

If a preprint has a formally published version:

- Cite the formal version
- Use the formal DOI when one exists
- If the formal version has no DOI, fetch BibTeX from DBLP or another reliable provider and mark the entry for manual second checking
- Prefer publisher or Crossref metadata over preprint metadata
- Keep the preprint only as a search breadcrumb in your notes, not in the final bibliography

If you cannot prove that a formal version exists, say so explicitly.

### 6. Rank candidates with a repeatable score

Use the scoring rubric in [references/quality-scoring.md](references/quality-scoring.md).

Use `scripts/score_papers.py` when you already have candidate metadata in JSON or JSONL form. The script computes:

- venue score
- freshness score
- citation score
- impact-factor score
- relevance score
- evidence score
- publication bonus
- rejection reasons

Treat the score as a ranking aid, not as proof. A lower-scoring paper that directly supports the claim can beat a higher-scoring but weakly related paper.

Apply a minimum relevance gate before delivery. If no candidate clears the gate, return no final citation rather than a weakly related paper.

### 7. Produce verified output

For each accepted citation, provide:

- the claim being supported
- 1 to 3 recommended papers ranked by fit
- DOI when verified, or an explicit note that no DOI was found for the formal version
- venue and year
- a brief explanation of why the paper supports the claim
- any caveat, such as the paper being older but seminal
- a manual-check note when the final BibTeX came from a trusted provider without a DOI

When generating BibTeX, include provenance fields such as:

- `doi`
- `url`
- `x-bib-source`
- `x-bib-source-url`
- `x-verified-with`
- `x-verified-at`
- `x-quality-score`
- `x-verification-status`
- `x-secondary-check-required`

Do not fabricate missing BibTeX fields. If the source record is incomplete, say what is missing.

After every run:

- sync accepted citations back into `.latex-citation-curator/verification-ledger.json`
- sync accepted citations into the user-level paper library
- if a target `.bib` file was provided, sync the ledger with that `.bib` file again
- keep user-edited BibTeX entries intact; use the ledger to store extra verification state instead of overwriting manual edits

## Hard Rules

- Do not invent papers, DOIs, venues, citation counts, quartiles, or impact factors.
- Do not claim authenticated Semantic Scholar verification when no valid user key was used.
- Do not keep a preprint in the final bibliography when a verified formal publication exists.
- If a formal version has a DOI, verify and use it.
- If a formal version has no DOI, only emit BibTeX from a trusted provider such as DBLP and mark the result for manual second checking.
- Do not use Google Scholar as an automated metadata or BibTeX source.
- Do not overwrite user-authored `.bib` entries just to inject verification metadata. Store extra state in the project ledger unless the user explicitly asks for BibTeX rewrites.
- Do not guess CCF tier or JCR quartile. Mark them as unknown when you cannot verify them.
- Do not hide uncertainty. Surface disagreements between DBLP, Crossref, Semantic Scholar, and publisher pages.

## Local Helpers

### `scripts/extract_citation_needs.py`

Run this on a local `.tex` file or on stdin to find paragraph-level citation gaps.

Example:

```bash
python3 scripts/extract_citation_needs.py draft.tex --json
```

### `scripts/score_papers.py`

Run this on JSON or JSONL candidate metadata to compute a consistent ranking.

Example:

```bash
python3 scripts/score_papers.py candidates.json --format tsv
```

Each candidate record may contain:

- `title`
- `year`
- `venue`
- `doi`
- `citationCount`
- `ccfTier`
- `jcrQuartile`
- `impactFactor`
- `relevanceScore`
- `evidenceScore`
- `peerReviewed`
- `preprint`
- `hasFormalVersion`
- `source`
- `sourceUrl`
- `reliableBibtexSource`
- `manualCheckRequired`

### `scripts/fetch_verified_bibtex.py`

Run this when you need an end-to-end verified citation workflow with live metadata sources.

It prefers a Semantic Scholar API key when available and will:

- search Semantic Scholar for candidates
- supplement candidate discovery and metadata with OpenAlex
- use Crossref and DOI resolution to confirm formal publication metadata
- use DBLP to add computer-science bibliographic evidence when available
- replace preprints with verified formal versions when found
- fall back to DBLP BibTeX for formal versions that have no DOI
- score the surviving candidates
- emit DOI-verified or trusted-source BibTeX with provenance fields

The script also:

- creates and maintains `.latex-citation-curator/verification-ledger.json` in the project root
- creates and maintains a user-level `paper-library.json` in the cache directory for cross-project reuse
- scans `--existing-bib`, `--append-bib`, and existing `--write-bib` targets before remote discovery
- prioritizes previously checked local BibTeX, project-ledger entries, and user-library entries before remote metadata lookups
- prompts once in interactive terminals to ask whether the user has a Semantic Scholar API key
- continues in free shared mode when the user does not have a key
- falls back to the shared Semantic Scholar channel when authenticated access is refused
- uses long backoff for shared-channel rate limits
- stores successful network responses in a persistent local cache file
- stores processed BibTeX and metadata in a persistent user-level paper library
- prints a progress box to stderr without mixing failed attempts into the final result set
- emits Google Scholar query links only as manual review hints, not as scraped metadata

The script first checks `--semantic-scholar-api-key`, then `SEMANTIC_SCHOLAR_API_KEY`, then the persisted local secret file.

Use `--no-key-prompt` in non-interactive wrappers when you want the script to skip the terminal prompt and proceed directly with stored or shared mode.

Use `--existing-bib refs.bib` to make the script inspect an existing bibliography first. Use `--project-root /path/to/project` to control where `.latex-citation-curator/` is created.

Examples:

```bash
python3 scripts/fetch_verified_bibtex.py \
  --query "multimodal retrieval remains effective in low-resource settings" \
  --existing-bib refs.bib \
  --write-json report.json \
  --write-bib verified.bib
```

```bash
python3 scripts/extract_citation_needs.py draft.tex --json > claims.json
python3 scripts/fetch_verified_bibtex.py \
  --claims-json claims.json \
  --existing-bib refs.bib \
  --no-key-prompt \
  --append-bib refs.bib
```

Provide `--venue-hints venue-hints.json` when you have curated CCF/JCR/IF mappings for recurring venues.

## Exception Handling

- Allow older papers when they are seminal, standards-defining, or required to explain a classic method. Label them as a `seminal exception`.
- Allow a non-CCF or non-JCR venue when the field is interdisciplinary and the paper is otherwise strongly supported by DOI, citation performance, and direct relevance.
- If only preprints exist and the user explicitly allows them, keep them outside the final verified bibliography or label them as `fallback preprint` instead of `verified final citation`.

## Recommended Optimizations

Apply these improvements when the task is large:

- Reuse one verified paper across multiple nearby claims instead of duplicating searches.
- Build a sidecar decision log in JSONL with claim, DOI, score, and rejection reason.
- Compare raw citations with citations per year to reduce age bias.
- Keep a field-specific venue allowlist when the manuscript repeatedly targets the same subdomain.
- Detect duplicate DOIs before appending new BibTeX entries.
- Detect duplicate BibTeX keys when DOI is unavailable.

## References

- Read [references/quality-scoring.md](references/quality-scoring.md) for the scoring formula and tie-break rules.
- Read [references/source-verification.md](references/source-verification.md) for DOI checks, preprint replacement, and BibTeX provenance requirements.
