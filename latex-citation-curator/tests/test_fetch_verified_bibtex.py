import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path("/mnt/d/Jacazjx/Documents/Playground/latex-citation-curator/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("fetch_verified_bibtex", SCRIPTS_DIR / "fetch_verified_bibtex.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SplitBibtexEntriesTest(unittest.TestCase):
    def test_keeps_latex_accent_quotes_inside_braces(self) -> None:
        text = r"""@article{federated-learning,
  author = {H. Brendan McMahan and Eider Moore and Daniel Ramage and Blaise Ag{\"{u}}era y Arcas},
  title = {Communication-Efficient Learning of Deep Networks from Decentralized Data}
}

@article{follow-up,
  title = {A Follow-Up Entry}
}
"""
        entries = MODULE.split_bibtex_entries(text)
        self.assertEqual(2, len(entries))
        self.assertIn(r'Ag{\"{u}}era y Arcas', entries[0])
        self.assertIn("@article{follow-up", entries[1])

    def test_still_handles_regular_quoted_values(self) -> None:
        text = """@misc{quoted,
  title = \"Quoted {Value}\",
  note = \"Example\"
}
"""
        entries = MODULE.split_bibtex_entries(text)
        self.assertEqual(1, len(entries))
        self.assertIn('title = "Quoted {Value}"', entries[0])


class OpenAlexLimitTest(unittest.TestCase):
    def test_openalex_limit_zero_skips_openalex_search(self) -> None:
        original_argv = sys.argv[:]
        original_resolve_key = MODULE.resolve_semantic_scholar_key
        original_semantic_search = MODULE.semantic_scholar_search
        original_sync_local_bib_entries = MODULE.sync_local_bib_entries
        original_save_ledger = MODULE.save_verification_ledger
        original_save_library = MODULE.save_user_library
        original_openalex_search = MODULE.openalex_search_query

        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["CODEX_CACHE_HOME"] = str(Path(temp_dir) / "cache")
            os.environ["CODEX_HOME"] = str(Path(temp_dir) / "codex")

            MODULE.resolve_semantic_scholar_key = lambda args: ("", "", "shared")
            MODULE.semantic_scholar_search = lambda *args, **kwargs: ([], "shared")
            MODULE.sync_local_bib_entries = lambda *args, **kwargs: []
            MODULE.save_verification_ledger = lambda *args, **kwargs: None
            MODULE.save_user_library = lambda *args, **kwargs: None

            def fail_openalex(*args, **kwargs):
                raise AssertionError("OpenAlex search should be skipped when --openalex-limit is 0")

            MODULE.openalex_search_query = fail_openalex
            sys.argv = [
                "fetch_verified_bibtex.py",
                "--query",
                "test query",
                "--openalex-limit",
                "0",
                "--no-key-prompt",
                "--no-progress",
                "--format",
                "json",
            ]

            try:
                exit_code = MODULE.main()
            finally:
                sys.argv = original_argv
                MODULE.resolve_semantic_scholar_key = original_resolve_key
                MODULE.semantic_scholar_search = original_semantic_search
                MODULE.sync_local_bib_entries = original_sync_local_bib_entries
                MODULE.save_verification_ledger = original_save_ledger
                MODULE.save_user_library = original_save_library
                MODULE.openalex_search_query = original_openalex_search
                os.environ.pop("CODEX_CACHE_HOME", None)
                os.environ.pop("CODEX_HOME", None)

        self.assertEqual(0, exit_code)


if __name__ == "__main__":
    unittest.main()
