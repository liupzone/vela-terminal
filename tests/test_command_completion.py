"""Tab completion for the file panel's command bar.

The bar navigates directories, so completion offers directories only: completing
a file name would produce something that cannot be entered.  Completion works on
the raw typed text rather than on a resolved path, so ``cd ~/ve`` completes to
``cd ~/vela-terminal`` and keeps the user's own spelling.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela.filebrowser import (  # noqa: E402
    _common_prefix,
    completion_candidates,
    split_for_completion,
)


class SplitTests(unittest.TestCase):
    def test_empty_line_has_nothing_to_complete(self):
        self.assertEqual(split_for_completion(""), ("", "", ""))

    def test_bare_cd_completes_the_command_first(self):
        self.assertEqual(split_for_completion("cd"), ("cd ", "", ""))

    def test_cd_with_trailing_space_offers_everything(self):
        self.assertEqual(split_for_completion("cd "), ("cd ", "", ""))

    def test_partial_name_keeps_the_command_prefix(self):
        self.assertEqual(split_for_completion("cd vela"), ("cd ", "", "vela"))

    def test_tilde_is_kept_verbatim(self):
        self.assertEqual(split_for_completion("cd ~/ve"), ("cd ", "~/", "ve"))

    def test_absolute_path_splits_at_the_last_slash(self):
        self.assertEqual(split_for_completion("cd /usr/sh"), ("cd ", "/usr/", "sh"))

    def test_a_bare_path_has_no_prefix(self):
        self.assertEqual(split_for_completion("/usr/sh"), ("", "/usr/", "sh"))

    def test_dot_prefixes_are_fragments(self):
        self.assertEqual(split_for_completion("cd .."), ("cd ", "", ".."))
        self.assertEqual(split_for_completion("cd ."), ("cd ", "", "."))

    def test_a_word_that_is_not_cd_is_a_bare_fragment(self):
        # "cdx" is not the cd command, so it is a path fragment to complete.
        self.assertEqual(split_for_completion("cdx"), ("", "", "cdx"))
        self.assertEqual(split_for_completion("c"), ("", "", "c"))

    def test_trailing_space_after_a_path_offers_its_contents(self):
        self.assertEqual(split_for_completion("cd /usr/"), ("cd ", "/usr/", ""))


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        for name in ("alpha", "alpine", "beta", "gamma", ".hidden"):
            os.makedirs(os.path.join(self.root, name), exist_ok=True)
        with open(os.path.join(self.root, "afile.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_directories_are_offered(self):
        names = completion_candidates("", "", self.root)
        self.assertNotIn("afile.txt", names)
        self.assertIn("alpha", names)

    def test_hidden_directories_need_a_dot(self):
        self.assertNotIn(".hidden", completion_candidates("", "", self.root))
        self.assertIn(".hidden", completion_candidates("", ".h", self.root))

    def test_fragment_filters(self):
        self.assertEqual(
            completion_candidates("", "al", self.root), ["alpha", "alpine"]
        )

    def test_no_match_is_empty(self):
        self.assertEqual(completion_candidates("", "zzz", self.root), [])

    def test_absolute_base_is_used(self):
        self.assertEqual(
            completion_candidates(self.root + "/", "", self.root),
            ["alpha", "alpine", "beta", "gamma"],
        )

    def test_missing_base_is_empty_not_an_error(self):
        self.assertEqual(completion_candidates("/no/such/dir-vela/", "", self.root), [])

    def test_results_are_sorted(self):
        names = completion_candidates("", "", self.root)
        self.assertEqual(names, sorted(names))


class CommonPrefixTests(unittest.TestCase):
    def test_single_name_is_itself(self):
        self.assertEqual(_common_prefix(["alpha"]), "alpha")

    def test_shared_prefix(self):
        self.assertEqual(_common_prefix(["alpha", "alpine"]), "alp")

    def test_nothing_shared(self):
        self.assertEqual(_common_prefix(["alpha", "beta"]), "")

    def test_empty_input(self):
        self.assertEqual(_common_prefix([]), "")


if __name__ == "__main__":
    unittest.main()
