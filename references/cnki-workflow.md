# CNKI Workflow

## Table of Contents

- Scope
- When to use CNKI
- Source priority
- Search strategy
- Browser-assisted CNKI search
- Advanced filters
- Result metadata
- Detail-page metadata
- Export metadata
- Verification and ranking rules
- Access and captcha handling

## Scope

Use this reference only for CNKI-related work inside the broader LaTeX Citation Curator workflow. Do not import a separate CNKI skill suite or make CNKI the default source for all citation work.

CNKI should add Chinese-language coverage, Chinese journal metadata, and source-category filters to the existing DOI-first citation verification workflow.

## When to use CNKI

Use CNKI when at least one is true:

- The user explicitly asks for CNKI, 中国知网, 中文文献, 中文论文, CSSCI, CSCD, 北大核心, or Chinese journal evidence.
- The claim concerns China-specific policy, education, medicine, law, management, social science, humanities, or local empirical data.
- English metadata sources return weak coverage for a Chinese-language topic.
- The user needs GB/T 7714, EndNote, RIS, or other CNKI-exported citation metadata.

Do not use CNKI as a replacement for Semantic Scholar, OpenAlex, Crossref, DOI resolution, or DBLP when those sources are better suited to the field.

## Source priority

For a Chinese-language citation gap:

1. Search local `.bib`, project ledger, and user paper library first.
2. Search Semantic Scholar and OpenAlex when the topic may have English or DOI-bearing records.
3. Search CNKI with Chinese terms and, when needed, CSSCI/CSCD/北大核心 filters.
4. Verify DOI-bearing candidates with Crossref, DOI resolution, publisher metadata, or other DOI-aware sources.
5. Use CNKI export metadata for no-DOI Chinese formal publications, with manual second-check marking.

## Search strategy

Generate both English and Chinese query terms when the draft is bilingual or the claim has an international equivalent. For Chinese-only topics, prefer Chinese terms first.

For each CNKI query, record:

- query string
- search field such as subject, title, keyword, abstract, or author
- source category filters used
- date range
- result count
- selected result URLs or export IDs
- observed captcha or access limitations

## Browser-assisted CNKI search

When a browser automation environment is available, navigate to CNKI search directly instead of relying on screenshots or OCR.

Basic search URL:

```text
https://kns.cnki.net/kns8s/search
```

Useful search-result selectors, when present:

| Data | Selector |
|---|---|
| Search input | `input.search-input` |
| Search button | `input.search-btn` |
| Result count | `.pagerTitleCell` |
| Page indicator | `.countPageMark` |
| Result rows | `.result-table-list tbody tr` |
| Title link | `td.name a.fz14` |
| Authors | `td.author a.KnowledgeNetLink` |
| Journal/source | `td.source a` |
| Date | `td.date` |
| Citations | `td.quote` |
| Downloads | `td.download` |
| Export ID | `.result-table-list tbody input.cbItem` |

If the environment lacks browser automation or CNKI access, ask the user to provide CNKI result links, exported citations, screenshots converted to text, RIS/EndNote records, or GB/T 7714 output. Do not pretend CNKI was searched.

## Advanced filters

Use the old-style advanced search only when source-category filters are needed.

Advanced search URL:

```text
https://kns.cnki.net/kns/AdvSearch?classid=7NS01R8M
```

Useful field and filter controls, when present:

| Purpose | Selector or value |
|---|---|
| Row 1 field | first visible `select`, common values: `SU`, `TI`, `KY`, `TKA`, `AB` |
| Row 1 keyword | `#txt_1_value1` |
| Row 2 keyword | `#txt_2_value1` |
| Author | `#au_1_value1` |
| Journal/source | `#magazine_value1` |
| Source all | `#gjAll` |
| SCI | `#SCI` |
| EI | `#EI` |
| 北大核心 | `#hx` |
| CSSCI | `#CSSCI` |
| CSCD | `#CSCD` |
| Submit | `div.search` |

Only record SCI, EI, CSSCI, CSCD, or 北大核心 if the label was explicitly selected or observed. Do not infer source category from journal name.

## Result metadata

From a result row, collect:

- title
- detail URL
- authors
- journal/source
- publication date
- CNKI citation count if present
- CNKI download count if present
- export ID if present

Citation and download counts are ranking hints only. Do not treat CNKI downloads as scholarly quality proof.

## Detail-page metadata

When visiting a CNKI detail page, prefer JavaScript metadata extraction from visible DOM over screenshot parsing.

Useful selectors, when present:

| Data | Selector |
|---|---|
| Main detail section | `.brief` |
| Title | `.brief h1` |
| Authors | `.brief h3.author:first-of-type a` |
| Affiliations | `.brief h3.author:nth-of-type(2) a` |
| Abstract | `.abstract-text` |
| Keywords | `p.keywords a` |
| Fund | `p.funds` |
| Classification | `.clc-code` |
| Journal/source | `.doc-top a` |
| Online-first marker | `.brief .icon-shoufa` |
| Citation tabs | `ul.module-tab.tpl_lieteratures li` |

Use detail-page abstract and keywords to judge claim fit. Do not rely on title similarity alone.

## Export metadata

CNKI export data can be used as a trusted bibliography source for Chinese formal publications, especially when no DOI exists.

Known export formats include:

- GB/T 7714 citation text
- EndNote
- RIS

When export metadata is used, record provenance fields such as:

```bibtex
x-bib-source = {cnki-export}
x-bib-source-url = {https://kns.cnki.net/...}
x-verified-with = {cnki}
x-verification-status = {trusted-bibtex-no-doi}
x-secondary-check-required = {true}
```

If a DOI is present in the CNKI export, verify it separately before setting `x-verification-status = {verified-doi}`.

## Verification and ranking rules

Use CNKI evidence this way:

- Treat CNKI as strong evidence that a Chinese-language formal publication exists when the detail page and export metadata align.
- Treat CNKI source-category filters as venue-quality hints only when explicitly observed.
- Treat CNKI citation/download counts as weak ranking features; direct claim fit, publication status, DOI verification, and venue/source quality matter more.
- Prefer a DOI-verified formal record over a CNKI-only no-DOI record when both support the claim equally well.
- Prefer CNKI-only records when the claim is specifically about Chinese practice, Chinese local data, or Chinese-language scholarship not covered well elsewhere.

For final output, include a caveat for CNKI-only entries:

```text
CNKI metadata was used as the trusted bibliography source; no DOI was verified, so this entry is marked for manual second checking.
```

## Access and captcha handling

CNKI may require institutional login or show Tencent captcha. Respect that access flow.

If a visible captcha appears, pause and tell the user:

```text
CNKI is showing a visible captcha. Please complete it manually in the browser, then tell me to continue.
```

Do not claim to have downloaded full text unless the user provided access and the download actually succeeded. For citation curation, metadata and citation export are usually sufficient; avoid downloading PDF/CAJ unless the user explicitly needs full-text review and has legitimate access.
