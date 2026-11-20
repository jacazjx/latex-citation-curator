---
name: latex-citation-curator
description: find, verify, rank, and generate doi-verified or trusted-source bibtex citations for latex manuscripts and research-writing workflows. use when codex needs to scan `.tex` files or draft prose for citation gaps such as `[cite]`, `[citation needed]`, `\todo{cite}`, or chinese prompts like `我想找一篇论文来支撑论点`; search for real supporting papers across semantic scholar, openalex, crossref, dblp, doi resolvers, and cnki for chinese-language scholarship; prefer recent high-quality venues such as ccf a/b, jcr q1/q2, cssci, cscd, and 北大核心 when verified; replace preprints with formally published versions; and emit bibtex with explicit provenance fields.
---

# LaTeX Citation Curator

## Overview

Find citation gaps in LaTeX drafts, retrieve real candidate papers, rank them with a repeatable quality formula, and produce provenance-aware BibTeX entries.

Only mark bibliographic metadata verified when the shared field checker actually passes. A DOI string, provider name, old status, date or score is not verification. Read [references/source-verification.md](references/source-verification.md) before finalizing or reusing citations; it defines the checks, statuses and report schema.

Keep metadata verification separate from claim support. The fetcher sets claim support to `not-reviewed`; read the relevant paper content before recommending it as support.

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
- For Chinese-language claims, ask whether CNKI access through a logged-in browser is available when CNKI-only or CSSCI/北大核心/CSCD evidence is needed

If the user has a Semantic Scholar key:

- use authenticated Semantic Scholar mode
- persist the key after the first successful authenticated run
- record that Semantic Scholar participated in verification

If the user does not have a key:

- continue with the free shared Semantic Scholar pool
- keep long backoff and retryable shared-mode retries enabled
- verify accepted papers with OpenAlex, Crossref, DOI resolution, DBLP, and CNKI when applicable
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
- Generate Chinese keywords too when the claim concerns Chinese context, Chinese policy, Chinese-language scholarship, CNKI-only journals, CSSCI, CSCD, or 北大核心 sources
- Keep one short note describing what kind of evidence is needed: benchmark result, survey, causal claim, system design, theory, dataset paper, policy evidence, or Chinese empirical study

Prefer direct support over topical similarity.

### 4. Gather real candidates from primary metadata sources

Use this source order:

0. Existing project `.bib` files and the project-local `.latex-citation-curator/verification-ledger.json`
0.5. The user-level persistent paper library in the cache directory
1. Semantic Scholar API and OpenAlex for candidate discovery and metadata cross-checking
2. Crossref and DOI resolver or publisher landing pages for final DOI confirmation and official metadata
3. DBLP for computer-science bibliographic records
4. CNKI for Chinese-language scholarship, Chinese journals, CSSCI/CSCD/北大核心 filtering, GB/T 7714/EndNote metadata, and Chinese abstracts or keywords
5. Google Scholar only as a manual hint source when DOI discovery fails or metadata is sparse

Before remote discovery, scan the local `.bib` files, the project ledger, and the user-level paper library. If a matching entry has schema-version-2 field evidence, rerun the checker against stored observations and current BibTeX before reuse. Legacy labels alone require new verification. The fetcher still performs remote discovery; reuse skips re-verification of passing cached entries, not necessarily all network calls.

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
- citation count when the source exposes it
- source URL
- BibTeX, EndNote, RIS, or GB/T 7714 source URL/provider when available

Preserve each source's original values before merging. Check title, complete ordered authors, year, venue, formal-publication status, DOI when present, supplied volume/issue/pages/article number, and actual BibTeX. Do not silently replace disagreeing values.

Every required metadata field needs matching observations from at least two distinct source families. Crossref and DOI content negotiation count as one family; CNKI and its export also count as one. For DOI-bearing papers, download the DOI bibliography and compare its DOI and paper fields. Missing exports and source failures cannot pass.

Use [references/source-verification.md](references/source-verification.md) for the exact checks and state schema.

Read [references/cnki-workflow.md](references/cnki-workflow.md) when the task needs CNKI search, CNKI metadata extraction, CNKI source-category filters, or Chinese citation export handling.

### 5. Replace preprints with the final published version

Treat arXiv or other preprints as discovery hints, not final citations.

If a preprint has a formally published version:

- Cite the formal version
- Use the formal DOI when one exists
- If no DOI is found for the formal version, fetch a trusted export and apply the same field checks; the export alone remains pending
- Prefer publisher or Crossref metadata over preprint metadata
- Keep the preprint only as a search breadcrumb in your notes, not in the final bibliography

If you cannot prove that a formal version exists, say so explicitly.

For Chinese articles with no DOI found, CNKI plus its own export is one source family. Keep the entry pending until required cross-source checks pass; then use `verified-metadata-no-doi`, never `verified-doi`. CNKI remains browser assisted, not a fetch-script integration.

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

The fetcher's relevance/evidence values are title/abstract/keyword heuristics. They do not verify experiments, theory or claim support. Keep `claimSupport.status = not-reviewed` until the relevant content is read and its location, supporting result, conditions and reviewer are recorded separately.

Treat the score as a ranking aid, not as proof. A lower-scoring paper that directly supports the claim can beat a higher-scoring but weakly related paper.

Apply a minimum relevance gate before delivery. If no candidate clears the gate, return no final citation rather than a weakly related paper.

For CNKI candidates, use CSSCI, CSCD, 北大核心, SCI, or EI source-category evidence as venue-quality hints only when observed in CNKI or another source. Do not infer these labels from journal reputation alone.

### 7. Produce verified output

Only entries passing all required field checks may enter verified bibliography. Passing states are `verified-doi` and `verified-metadata-no-doi`; other states are `pending-verification`, `metadata-conflict`, or `verification-failed`. The old `trusted-bibtex-no-doi` label does not establish verification.

For each metadata-verified entry, provide:

- the claim being supported
- 1 to 3 recommended papers ranked by fit
- DOI when verified, or an explicit note that no DOI was found for the formal version
- venue and year
- a brief explanation of why the paper supports the claim
- any caveat, such as the paper being older but seminal or CNKI-only metadata requiring manual second check
- whether claim support was actually reviewed, separately from metadata verification

For each attempted entry that did not pass, retain `fieldChecks`, `metadataObservations` with URLs and original values, `unresolvedFields`, and fetch errors. Show unresolved fields and reasons in the report; exclude these entries from verified `.bib` output instead of silently filtering them away.

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
- `x-verification-schema`
- `x-verification-scope`
- `x-claim-support-status`

Do not fabricate missing BibTeX fields. If the source record is incomplete, say what is missing.

After every run:

- sync attempted citations and their checks, including failures, into `.latex-citation-curator/verification-ledger.json`
- sync those records into the user-level paper library
- if a target `.bib` file was provided, sync the ledger with that `.bib` file again
- keep user-edited BibTeX entries intact; use the ledger to store extra verification state instead of overwriting manual edits
- re-evaluate current `.bib` content against stored evidence; edits or failed rechecks revoke passing status in both stores

## Hard Rules

- Do not invent papers, DOIs, venues, citation counts, quartiles, source-category labels, or impact factors.
- Do not claim authenticated Semantic Scholar verification when no valid user key was used.
- Do not claim CNKI verification unless CNKI was actually searched or a CNKI record/export supplied the metadata.
- Do not bypass CNKI login, paywalls, download restrictions, or captchas. If CNKI shows a visible captcha, pause and ask the user to complete it manually.
- Do not keep a preprint in the final bibliography when a verified formal publication exists.
- If a formal version has a DOI, verify and use it.
- With no DOI found, require a trusted export and passing field checks before emitting verified BibTeX; pending/conflicting entries remain in reports only.
- Do not use Google Scholar as an automated metadata or BibTeX source.
- Do not overwrite user-authored `.bib` entries just to inject verification metadata. Store extra state in the project ledger unless the user explicitly asks for BibTeX rewrites.
- Do not guess CCF tier, JCR quartile, CSSCI, CSCD, or 北大核心 status. Mark them as unknown when you cannot verify them.
- Do not hide uncertainty. Surface disagreements between DBLP, Crossref, Semantic Scholar, OpenAlex, CNKI, and publisher pages.
- Never infer verification from a DOI, old `checked` flag, date, manual-check marker or score. Use actual observations and output BibTeX.
- Do not equate bibliographic verification with support for a manuscript claim.

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

Ranking inputs may contain the following fields. Without actual verification observations they can be ranked, but cannot become eligible for verified bibliography:

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

Verified eligibility also needs source observations, DOI resolution when applicable, actual BibTeX and parsed output metadata. See [references/source-verification.md](references/source-verification.md).

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
- compare actual source values and BibTeX, then emit only metadata-verified entries
- retain pending/failed records in `reviewRequired` with original values and reasons

The script also:

- creates and maintains `.latex-citation-curator/verification-ledger.json` in the project root
- creates and maintains a user-level `paper-library.json` in the cache directory for cross-project reuse
- scans `--existing-bib`, `--append-bib`, and existing `--write-bib` targets before remote discovery
- loads local entries first and reruns stored field checks before reusing a verified entry
- prompts once in interactive terminals to ask whether the user has a Semantic Scholar API key
- continues in free shared mode when the user does not have a key
- falls back to the shared Semantic Scholar channel when authenticated access is refused
- uses long backoff for shared-channel rate limits
- stores successful network responses in a persistent local cache file
- stores processed BibTeX and metadata in a persistent user-level paper library
- sends transient network progress to stderr while retaining unresolved candidate checks in reports and state
- emits Google Scholar query links only as manual review hints, not as scraped metadata

The script first checks `--semantic-scholar-api-key`, then `SEMANTIC_SCHOLAR_API_KEY`, then the persisted local secret file.

Use `--no-key-prompt` in non-interactive wrappers when you want the script to skip the terminal prompt and proceed directly with stored or shared mode.

Use `--existing-bib refs.bib` to make the script inspect an existing bibliography first. Use `--project-root /path/to/project` to control where `.latex-citation-curator/` is created.

`--write-bib` writes/replaces its output path; use a separate new file unless replacement was requested. `--append-bib` preserves existing entries and appends passing new entries.

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
- Allow a non-CCF, non-JCR, or non-CNKI-indexed venue when the field is interdisciplinary and the paper is otherwise strongly supported by DOI, citation performance, and direct relevance.
- If only preprints exist and the user explicitly allows them, keep them outside the final verified bibliography or label them as `fallback preprint` instead of `verified final citation`.

## Recommended Optimizations

Apply these improvements when the task is large:

- Reuse one verified paper across multiple nearby claims instead of duplicating searches.
- Build a sidecar decision log in JSONL with claim, DOI, score, and rejection reason.
- Compare raw citations with citations per year to reduce age bias.
- Keep a field-specific venue allowlist when the manuscript repeatedly targets the same subdomain.
- Detect duplicate DOIs before appending new BibTeX entries.
- Detect duplicate BibTeX keys when DOI is unavailable.
- For Chinese claims, run one broad Chinese CNKI query before deep per-claim searches so shared Chinese terms, authors, and journals can be reused.

## References

- Read [references/quality-scoring.md](references/quality-scoring.md) for the scoring formula and tie-break rules.
- Read [references/source-verification.md](references/source-verification.md) for DOI checks, preprint replacement, CNKI provenance handling, and BibTeX provenance requirements.
- Read [references/cnki-workflow.md](references/cnki-workflow.md) for CNKI search, advanced filters, result parsing, detail-page metadata, captcha handling, and export-provider rules.
