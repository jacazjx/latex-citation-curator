# Quality Scoring

## Table of Contents

- Formula
- Inputs
- Venue score
- Freshness score
- Citation score
- Impact score
- Relevance and evidence
- Publication bonus
- Rejection rules
- Tie-breaks
- Seminal exceptions

## Formula

Use this formula to rank candidates after collecting real metadata:

```text
quality_score =
    venue_score
  + freshness_score
  + citation_score
  + impact_score
  + relevance_score
  + evidence_score
  + publication_bonus
```

Cap the score at `100` only for display if needed. Keep raw subscores for auditing.

## Inputs

Prepare these fields for each candidate when possible:

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

Do not invent unavailable fields. Use `unknown`, `null`, or omit the field.

## Venue Score

Choose the larger of the CCF-based score and the JCR-based score.

### CCF mapping

- `A` -> `35`
- `B` -> `28`
- `C` -> `18`

### JCR mapping

- `Q1` -> `30`
- `Q2` -> `22`
- `Q3` -> `14`
- `Q4` -> `8`

### Fallback

- Peer-reviewed venue but tier unknown -> `10`
- Otherwise -> `0`

Do not guess CCF or JCR labels. If you cannot verify them, leave them unknown and use the fallback.

## Freshness Score

Prefer the last five years, but do not automatically discard older seminal work.

```text
age = current_year - year
freshness_score = max(0, 20 - 4 * max(age - 1, 0))
```

This gives:

- age `0` or `1` -> `20`
- age `2` -> `16`
- age `3` -> `12`
- age `4` -> `8`
- age `5` -> `4`
- age `6+` -> `0`

## Citation Score

Use both total citations and citations per year to reduce age bias.

```text
citations_per_year = citationCount / max(1, current_year - year + 1)
citation_score =
    min(15, 5 * log10(1 + citationCount))
  + min(10, 4 * log10(1 + citations_per_year))
```

If citation count is unavailable, use `0` for both citation terms and record the uncertainty.

## Impact Score

Use impact factor only for journals. Do not apply it to conferences.

```text
impact_score = min(15, 6 * log10(1 + impactFactor))
```

If impact factor is unavailable or not meaningful for the venue type, use `0`.

## Relevance and Evidence

Assign these manually after reading the abstract, metadata, and if necessary the paper landing page.

### Relevance Score

Score from `0` to `10`.

- `10`: directly supports the exact claim
- `7`: strongly relevant but narrower or broader than the claim
- `4`: topically related but indirect
- `0`: weak match

### Evidence Score

Score from `0` to `10`.

- `10`: strong empirical or theoretical support directly tied to the claim
- `7`: good evidence with some mismatch in setting or metric
- `4`: suggestive but weak support
- `0`: not enough evidence

## Publication Bonus

Add `5` when all of these are true:

- the paper has a DOI
- the final published version is verified
- provenance is recorded with `source` and `sourceUrl`

Add `3` when all of these are true:

- the final published version has no DOI
- BibTeX was downloaded from a trusted provider such as DBLP
- provenance is recorded with `source` and `sourceUrl`

Otherwise add `0`.

## Rejection Rules

Reject the candidate from the final verified list when any of these apply:

- DOI is missing and there is no trusted BibTeX provider for the formal version
- provenance is missing
- title, year, or author metadata disagree across sources and cannot be reconciled
- the record is a preprint and a formal published version is verified
- the record is only a preprint and the user did not explicitly allow fallback preprints
- the record has no DOI, no trusted-provider BibTeX, and no manual second-check path

Rejected items may remain in an audit trail, but not in the final verified bibliography.

## Tie-Breaks

When two papers have similar scores, prefer this order:

1. Better claim fit
2. Final published version over preprint
3. DOI-verified record over trusted-provider no-DOI record
4. Newer paper
5. Higher venue tier
6. Cleaner provenance chain

## Seminal Exceptions

Allow an older paper to beat a recent one when at least one is true:

- it introduced the method or benchmark being discussed
- it is the canonical citation used by the field
- it is a standards or survey paper the reader will expect

Label the decision explicitly as `seminal exception`.
