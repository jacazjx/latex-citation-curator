"""Regression cases use synthetic metadata and mock all remote services."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_verified_bibtex as fetch
from score_papers import compute_scores
from verification import apply_checks, is_verified


TITLE = "Federated optimization under statistical heterogeneity"
DOI = "10.1234/fixture"
AUTHORS = ["Ada Researcher", "Bo Scientist"]
VENUE = "Example Proceedings"
BIB = """@inproceedings{fixture,
  title = {Federated optimization under statistical heterogeneity},
  author = {Ada Researcher and Bo Scientist},
  year = {2025},
  booktitle = {Example Proceedings},
  doi = {10.1234/fixture}
}
"""


def semantic():
    return {"title": TITLE, "year": 2025, "venue": VENUE,
            "authors": [{"name": n} for n in AUTHORS],
            "externalIds": {"DOI": DOI}, "publicationTypes": ["Conference"],
            "url": "https://example.test/s2/fixture", "abstract": "A theoretical proof and experiment."}


def crossref():
    return {"title": [TITLE], "DOI": DOI, "type": "proceedings-article",
            "author": [{"given": "Ada", "family": "Researcher"}, {"given": "Bo", "family": "Scientist"}],
            "container-title": [VENUE], "published-print": {"date-parts": [[2025]]},
            "URL": "https://doi.org/" + DOI}


def openalex():
    return {"display_name": TITLE, "doi": "https://doi.org/" + DOI,
            "publication_year": 2025, "type_crossref": "proceedings-article",
            "authorships": [{"author": {"display_name": n}} for n in AUTHORS],
            "primary_location": {"version": "publishedVersion", "source": {"display_name": VENUE}},
            "id": "https://example.test/openalex/fixture"}


@contextlib.contextmanager
def services(cr=None, oa=None, bib=BIB, s2=None, dblp=None):
    cr = crossref() if cr is None else cr
    oa = openalex() if oa is None else oa
    s2 = semantic() if s2 is None else s2
    with contextlib.ExitStack() as stack:
        mocks = {
            "crossref_lookup_doi": cr, "crossref_search_title": [cr] if cr else [],
            "openalex_lookup_doi": oa, "openalex_search_title": [oa] if oa else [],
            "openalex_search_query": [oa] if oa else [], "doi_bibtex_lookup": bib,
            "dblp_search_title": dblp or [], "dblp_bibtex_lookup": bib,
            "semantic_scholar_search": ([s2], "shared"),
            "resolve_semantic_scholar_key": ("", "", "shared"),
        }
        for name, value in mocks.items():
            if isinstance(value, Exception):
                stack.enter_context(patch.object(fetch, name, side_effect=value))
            else:
                stack.enter_context(patch.object(fetch, name, return_value=value))
        yield


def enrich(candidate=None):
    candidate = fetch.semantic_scholar_record(semantic(), TITLE) if candidate is None else candidate
    return fetch.enrich_with_verified_metadata(candidate, query=TITLE, user_agent="test", crossref_limit=5,
        dblp_limit=5, openalex_limit=5, crossref_mailto=None, allow_preprint=False, venue_hints={})


class MetadataVerificationTest(unittest.TestCase):
    def test_matching_sources_and_downloaded_bibtex_pass(self):
        with services():
            result = enrich()
        self.assertTrue(result["metadataVerified"])
        self.assertTrue(result["eligible"])
        self.assertEqual("verified-doi", result["verificationStatus"])
        self.assertTrue(is_verified(result))
        self.assertEqual([], result["unresolvedFields"])
        self.assertEqual("not-reviewed", result["claimSupport"]["status"])
        self.assertIn("x-verification-scope = {bibliographic-metadata}", result["bibtex"])

    def test_core_conflicts_are_not_overwritten(self):
        for field in ("title", "authors", "year", "venue", "doi"):
            with self.subTest(field=field):
                cr = crossref()
                if field == "title": cr["title"] = ["A different paper"]
                elif field == "authors": cr["author"].reverse()
                elif field == "year": cr["published-print"]["date-parts"] = [[2024]]
                elif field == "venue": cr["container-title"] = ["Another venue"]
                else: cr["DOI"] = "10.1234/other"
                with services(cr=cr): result = enrich()
                self.assertFalse(result["eligible"])
                self.assertEqual("conflict", result["fieldChecks"][field]["status"])
                self.assertIn(field, result["unresolvedFields"])
                self.assertEqual(TITLE, result["title"])
                self.assertTrue(result["fieldChecks"][field]["observations"])

    def test_wrong_bibtex_doi_is_not_verified(self):
        with services(bib=BIB.replace(DOI, "10.1234/wrong")): result = enrich()
        self.assertFalse(result["doiResolution"]["passed"])
        self.assertFalse(result["metadataVerified"])
        self.assertEqual("conflict", result["fieldChecks"]["doi"]["status"])

    def test_wrong_bibtex_fields_block_export(self):
        with services(bib=BIB.replace("Bo Scientist", "Cy Writer")): result = enrich()
        self.assertFalse(result["eligible"])
        self.assertEqual("failed", result["fieldChecks"]["bibtex"]["status"])
        self.assertIn("authors", [d["field"] for d in result["fieldChecks"]["bibtex"]["differences"]])

    def test_missing_doi_export_is_not_success(self):
        with services(bib=fetch.FetchError("fixture timeout")): result = enrich()
        self.assertFalse(result["eligible"])
        self.assertEqual("insufficient-evidence", result["fieldChecks"]["doi"]["status"])
        self.assertEqual("doi-content-negotiation", result["verificationErrors"][0]["source"])

    def test_crossref_and_its_doi_export_are_one_source_family(self):
        candidate = fetch.crossref_record(crossref(), TITLE)
        candidate["candidateSource"] = "discovery"
        with services(oa={}): result = enrich(candidate)
        self.assertFalse(result["metadataVerified"])
        self.assertEqual("insufficient-evidence", result["fieldChecks"]["title"]["status"])

    def test_unreported_optional_fields_are_explicit(self):
        with services(): result = enrich()
        for field in ("volume", "issue", "pages", "articleNumber"):
            self.assertEqual("not-reported", result["fieldChecks"][field]["status"])
            self.assertFalse(result["fieldChecks"][field]["required"])

    def test_unchecked_volume_cannot_hide_behind_verified_identity(self):
        cr = crossref(); cr["volume"] = "12"
        bib = BIB.replace("  year", "  volume = {12},\n  year")
        with services(cr=cr, bib=bib): result = enrich()
        self.assertFalse(result["metadataVerified"])
        self.assertEqual("insufficient-evidence", result["fieldChecks"]["volume"]["status"])

    def test_matching_volume_issue_pages_are_verified(self):
        cr = crossref(); cr.update(volume="12", issue="3", page="1-9")
        oa = openalex(); oa["biblio"] = {"volume": "12", "issue": "3", "first_page": "1", "last_page": "9"}
        bib = BIB.replace("  year", "  volume = {12},\n  number = {3},\n  pages = {1--9},\n  year")
        with services(cr=cr, oa=oa, bib=bib): result = enrich()
        self.assertTrue(result["metadataVerified"])

    def test_different_issue_is_retained(self):
        cr = crossref(); cr["issue"] = "3"
        oa = openalex(); oa["biblio"] = {"issue": "4"}
        bib = BIB.replace("  year", "  number = {3},\n  year")
        with services(cr=cr, oa=oa, bib=bib): result = enrich()
        self.assertEqual("conflict", result["fieldChecks"]["issue"]["status"])
        self.assertFalse(result["eligible"])

    def test_missing_authors_cannot_pass(self):
        cr = crossref(); cr["author"] = []
        oa = openalex(); oa["authorships"] = []
        candidate = fetch.semantic_scholar_record(semantic(), TITLE); candidate["authors"] = []
        bib = BIB.replace("  author = {Ada Researcher and Bo Scientist},\n", "")
        with services(cr=cr, oa=oa, bib=bib): result = enrich(candidate)
        self.assertFalse(result["metadataVerified"])
        self.assertEqual("missing", result["fieldChecks"]["authors"]["status"])

    def test_scores_do_not_establish_verification(self):
        record = dict(title=TITLE, doi=DOI, year=2025, source="crossref", sourceUrl="https://example.test",
                      verificationStatus="verified-doi", checked=True, verifiedAt="2025-01-01",
                      relevanceScore=10, evidenceScore=10, ccfTier="A", citationCount=10000)
        result = compute_scores(record, 2026, False)
        self.assertFalse(result["eligible"])
        self.assertFalse(result["metadataVerified"])
        self.assertEqual(0, result["publicationBonus"])

    def test_trusted_no_doi_needs_cross_source_agreement(self):
        candidate = fetch.semantic_scholar_record(semantic(), TITLE); candidate["doi"] = ""
        hit = {"info": {"title": TITLE, "year": "2025", "venue": VENUE, "type": "Conference and Workshop Papers",
                        "authors": {"author": AUTHORS}, "url": "https://example.test/dblp/fixture"}}
        bib = BIB.replace("  doi = {10.1234/fixture}\n", "")
        with services(cr={}, oa={}, bib=bib, dblp=[hit]): result = enrich(candidate)
        self.assertTrue(result["eligible"])
        self.assertEqual("verified-metadata-no-doi", result["verificationStatus"])
        self.assertEqual("not-reported", result["fieldChecks"]["doi"]["status"])

    def test_preprint_is_not_promoted_on_similar_title_alone(self):
        candidate = fetch.semantic_scholar_record(semantic(), TITLE)
        candidate.update(preprint=True, publicationStatus="preprint", authors=["Different Author"])
        with services(): result = enrich(candidate)
        self.assertFalse(result["metadataVerified"])
        self.assertFalse(result.get("hasFormalVersion", False))

    def test_duplicate_discovery_records_retain_disagreement(self):
        first = fetch.semantic_scholar_record(semantic(), TITLE)
        oa = openalex(); oa["publication_year"] = 2024
        second = fetch.openalex_record(oa, TITLE)
        candidate = fetch.merge_candidates([first], [second])[0]
        with services(): result = enrich(candidate)
        self.assertEqual("conflict", result["fieldChecks"]["year"]["status"])


class StoredEvidenceTest(unittest.TestCase):
    def test_legacy_flags_dates_and_manual_markers_never_count_as_checks(self):
        for record in ({"checked": True}, {"verificationStatus": "verified-doi"},
                       {"verifiedAt": "2025-01-01"}, {"manualCheckRequired": True}):
            self.assertFalse(fetch.infer_local_checked_status(record))

    def test_actual_evidence_can_be_reused(self):
        with services(): result = enrich()
        reused = fetch.build_scored_local_result(result, query=TITLE, allow_preprint=False, venue_hints={})
        self.assertTrue(reused["metadataVerified"])
        self.assertEqual("not-reviewed", reused["claimSupport"]["status"])

    def test_scorer_reparses_actual_bibtex_instead_of_trusting_stored_parse(self):
        with services(): result = enrich()
        result["bibtex"] = result["bibtex"].replace("Bo Scientist", "Changed Author")
        scored = compute_scores(result, 2026, False)
        self.assertFalse(scored["metadataVerified"])
        self.assertEqual("failed", scored["fieldChecks"]["bibtex"]["status"])

    def test_resolution_of_another_doi_cannot_be_reused(self):
        with services(): result = enrich()
        result["doiResolution"]["requestedDoi"] = "10.1234/other"
        result["doiResolution"]["returnedDoi"] = "10.1234/other"
        self.assertFalse(apply_checks(result)["metadataVerified"])
        self.assertEqual("insufficient-evidence", result["fieldChecks"]["doi"]["status"])

    def test_removing_output_doi_does_not_hide_an_observed_doi(self):
        with services(): result = enrich()
        result["doi"] = ""
        result["bibtex"] = result["bibtex"].replace("  doi = {10.1234/fixture}", "  note = {Fixture}")
        result = apply_checks(result)
        self.assertFalse(result["metadataVerified"])
        self.assertIn("doi", result["unresolvedFields"])

    def test_user_edit_invalidates_ledger_without_overwriting_bibtex(self):
        with services(): result = enrich()
        ledger = {"entries": {}}
        fetch.update_ledger_entry(ledger, result)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "refs.bib"
            changed = result["bibtex"].replace("Bo Scientist", "Changed Author")
            path.write_text(changed, encoding="utf-8")
            items = fetch.sync_local_bib_entries([path], ledger)
            self.assertFalse(items[0]["checked"])
            self.assertEqual("conflict", items[0]["fieldChecks"]["authors"]["status"])
            self.assertEqual(changed, path.read_text(encoding="utf-8"))
            self.assertIn("Changed Author", items[0]["bibtex"])
            self.assertFalse(ledger["entries"]["doi:" + DOI]["checked"])

    def test_failed_recheck_clears_success_in_both_stores(self):
        with services(): good = enrich()
        cr = crossref(); cr["published-print"]["date-parts"] = [[2024]]
        with services(cr=cr): failed = enrich()
        for updater in (fetch.update_ledger_entry, fetch.update_user_library_entry):
            store = {"entries": {}}
            updater(store, good); updater(store, failed)
            saved = store["entries"]["doi:" + DOI]
            self.assertFalse(saved["checked"])
            self.assertEqual("metadata-conflict", saved["verificationStatus"])

    def test_parser_does_not_confuse_provenance_doi_with_actual_doi(self):
        entry = "@article{x, title={Fixture}, x-verification-doi={10.1234/not-real-field}}"
        self.assertEqual("", fetch.bibtex_field_value(entry, "doi"))


class ReportTest(unittest.TestCase):
    def test_cli_retains_failed_fields_and_excludes_them_from_bibtex(self):
        cr = crossref(); cr["published-print"]["date-parts"] = [[2024]]
        with tempfile.TemporaryDirectory() as temp, services(cr=cr):
            root = Path(temp)
            argv = ["fetch_verified_bibtex.py", "--query", TITLE, "--project-root", str(root),
                    "--write-json", str(root / "report.json"), "--write-bib", str(root / "new.bib"),
                    "--no-key-prompt", "--no-progress", "--format", "json"]
            with patch.dict(os.environ, {"CODEX_CACHE_HOME": str(root / "cache"), "CODEX_HOME": str(root / "codex")}), patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, fetch.main())
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual([], report[0]["results"])
            self.assertEqual("conflict", report[0]["reviewRequired"][0]["fieldChecks"]["year"]["status"])
            self.assertEqual("", (root / "new.bib").read_text(encoding="utf-8").strip())
            ledger = json.loads((root / ".latex-citation-curator/verification-ledger.json").read_text(encoding="utf-8"))
            self.assertFalse(ledger["entries"]["doi:" + DOI]["checked"])
            rendered = fetch.render_markdown(report, 3)
            self.assertIn("year: conflict", rendered)
            self.assertIn("2024", rendered)


if __name__ == "__main__":
    unittest.main()
