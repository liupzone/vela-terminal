"""Font discovery, archive validation and installation.

Nothing here touches the network: the downloader is injected, and archives are
built in memory so the failure modes (HTML error page instead of a zip, archive
without fonts, truncated file) can be tested deterministically.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import fonts  # noqa: E402


def make_font_bytes(size: int = 64) -> bytes:
    """A minimal file with a valid TrueType magic."""
    return b"\x00\x01\x00\x00" + b"\x00" * max(0, size - 4)


def make_zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class MagicTests(unittest.TestCase):
    def test_true_type(self):
        self.assertTrue(fonts.looks_like_font(b"\x00\x01\x00\x00rest"))

    def test_opentype_cff(self):
        self.assertTrue(fonts.looks_like_font(b"OTTO...."))

    def test_woff(self):
        self.assertTrue(fonts.looks_like_font(b"wOFF...."))

    def test_html_is_rejected(self):
        """A failed download commonly returns an error page."""
        self.assertFalse(fonts.looks_like_font(b"<!DOCTYPE html><html>"))

    def test_empty_is_rejected(self):
        self.assertFalse(fonts.looks_like_font(b""))

    def test_text_is_rejected(self):
        self.assertFalse(fonts.looks_like_font(b"Not Found"))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = self._temp.name

    def tearDown(self):
        self._temp.cleanup()

    def write_zip(self, entries):
        path = os.path.join(self.root, "pkg.zip")
        with open(path, "wb") as handle:
            handle.write(make_zip(entries))
        return path

    def test_finds_fonts(self):
        path = self.write_zip(
            {
                "fonts/A.ttf": make_font_bytes(),
                "fonts/B.otf": b"OTTO" + b"\x00" * 60,
                "README.md": b"# docs",
                "LICENSE": b"OFL",
            }
        )
        names = fonts.font_files_in_archive(path)
        self.assertEqual(sorted(names), ["fonts/A.ttf", "fonts/B.otf"])

    def test_skips_non_font_extension(self):
        path = self.write_zip({"fonts/A.ttf": make_font_bytes(), "a.txt": b"x"})
        self.assertEqual(fonts.font_files_in_archive(path), ["fonts/A.ttf"])

    def test_rejects_file_with_font_extension_but_wrong_content(self):
        """An HTML page saved as .ttf must not be installed."""
        path = self.write_zip({"fonts/bad.ttf": b"<!DOCTYPE html>"})
        self.assertEqual(fonts.font_files_in_archive(path), [])

    def test_prefers_static_over_variable(self):
        path = self.write_zip(
            {
                "fonts/variable/A[wght].ttf": make_font_bytes(),
                "fonts/ttf/A-Regular.ttf": make_font_bytes(),
            }
        )
        names = fonts.font_files_in_archive(path)
        self.assertEqual(names, ["fonts/ttf/A-Regular.ttf"])

    def test_falls_back_to_variable_when_no_static(self):
        path = self.write_zip({"fonts/variable/A[wght].ttf": make_font_bytes()})
        self.assertEqual(fonts.font_files_in_archive(path), ["fonts/variable/A[wght].ttf"])

    def test_not_a_zip(self):
        path = os.path.join(self.root, "nope.zip")
        with open(path, "wb") as handle:
            handle.write(b"this is not a zip")
        self.assertEqual(fonts.font_files_in_archive(path), [])

    def test_missing_file(self):
        self.assertEqual(
            fonts.font_files_in_archive(os.path.join(self.root, "gone.zip")), []
        )


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = self._temp.name

    def tearDown(self):
        self._temp.cleanup()

    def test_installs_fonts_into_a_package_directory(self):
        data = make_zip(
            {"fonts/A.ttf": make_font_bytes(), "fonts/B.ttf": make_font_bytes()}
        )
        installed = fonts.install_archive(data, "demo", self.root)
        self.assertEqual(len(installed), 2)
        self.assertTrue(all(os.path.exists(path) for path in installed))
        package_dir = os.path.join(self.root, "demo")
        self.assertEqual(sorted(os.listdir(package_dir)), ["A.ttf", "B.ttf"])

    def test_layout_is_flattened(self):
        data = make_zip({"deep/nested/dir/C.ttf": make_font_bytes()})
        installed = fonts.install_archive(data, "demo", self.root)
        self.assertEqual(installed, [os.path.join(self.root, "demo", "C.ttf")])

    def test_reinstall_replaces_instead_of_accumulating(self):
        first = make_zip({"a.ttf": make_font_bytes()})
        fonts.install_archive(first, "demo", self.root)
        second = make_zip({"b.ttf": make_font_bytes()})
        installed = fonts.install_archive(second, "demo", self.root)
        self.assertEqual(len(installed), 1)
        self.assertEqual(os.listdir(os.path.join(self.root, "demo")), ["b.ttf"])

    def test_archive_without_fonts_is_rejected(self):
        data = make_zip({"README.md": b"docs only"})
        with self.assertRaises(fonts.DownloadError):
            fonts.install_archive(data, "demo", self.root)

    def test_empty_archive_is_rejected(self):
        with self.assertRaises(fonts.DownloadError):
            fonts.install_archive(b"", "demo", self.root)

    def test_html_instead_of_zip_is_rejected(self):
        with self.assertRaises(fonts.DownloadError):
            fonts.install_archive(b"<!DOCTYPE html>", "demo", self.root)

    def test_no_temp_files_left_behind(self):
        data = make_zip({"a.ttf": make_font_bytes()})
        fonts.install_archive(data, "demo", self.root)
        leftovers = [n for n in os.listdir(self.root) if n.endswith(".zip")]
        self.assertEqual(leftovers, [])

    def test_no_temp_files_left_behind_on_failure(self):
        temp_dir = tempfile.gettempdir()
        before = set(os.listdir(temp_dir))
        with self.assertRaises(fonts.DownloadError):
            fonts.install_archive(make_zip({"x.txt": b"no fonts"}), "demo", self.root)
        after = set(os.listdir(temp_dir))
        created = {n for n in after - before if n.endswith(".zip")}
        self.assertEqual(created, set())

    def test_installed_font_files_lists_everything(self):
        fonts.install_archive(make_zip({"a.ttf": make_font_bytes()}), "one", self.root)
        fonts.install_archive(make_zip({"b.otf": b"OTTO" + b"\x00" * 60}), "two", self.root)
        found = fonts.installed_font_files(self.root)
        self.assertEqual(len(found), 2)

    def test_installed_font_files_missing_directory(self):
        self.assertEqual(fonts.installed_font_files(os.path.join(self.root, "no")), [])


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class DownloadTests(unittest.TestCase):
    def test_successful_download(self):
        data = fonts.download("https://example.invalid/f.zip", opener=lambda *a, **k: FakeResponse(b"PK\x03\x04"))
        self.assertEqual(data, b"PK\x03\x04")

    def test_empty_response_is_an_error(self):
        with self.assertRaises(fonts.DownloadError):
            fonts.download("https://example.invalid/f.zip", opener=lambda *a, **k: FakeResponse(b""))

    def test_http_error_is_reported(self):
        import urllib.error

        def boom(*_args, **_kwargs):
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

        with self.assertRaises(fonts.DownloadError) as context:
            fonts.download("https://example.invalid/f.zip", opener=boom)
        self.assertIn("404", str(context.exception))

    def test_network_error_is_reported(self):
        import urllib.error

        def boom(*_args, **_kwargs):
            raise urllib.error.URLError("no route to host")

        with self.assertRaises(fonts.DownloadError) as context:
            fonts.download("https://example.invalid/f.zip", opener=boom)
        self.assertIn("网络", str(context.exception))

    def test_install_uses_injected_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = fonts.find_candidate("jetbrains-mono")
            self.assertIsNotNone(entry)
            original = fonts.download
            try:
                fonts.download = lambda *a, **k: make_zip(
                    {"a.ttf": make_font_bytes()}
                )
                installed = fonts.install("jetbrains-mono", directory)
            finally:
                fonts.download = original
            self.assertEqual(len(installed), 1)

    def test_unknown_font_key(self):
        with self.assertRaises(fonts.DownloadError):
            fonts.install("no-such-font", tempfile.gettempdir())


class CatalogTests(unittest.TestCase):
    def test_catalog_is_not_empty(self):
        self.assertGreaterEqual(len(fonts.catalog()), 5)

    def test_keys_are_unique(self):
        keys = [entry.key for entry in fonts.catalog()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_names_are_unique(self):
        names = [entry.name for entry in fonts.catalog()]
        self.assertEqual(len(names), len(set(names)))

    def test_every_entry_has_a_https_url(self):
        for entry in fonts.catalog():
            with self.subTest(font=entry.key):
                self.assertTrue(entry.url.startswith("https://"), entry.url)
                self.assertTrue(entry.description)

    def test_find_candidate(self):
        self.assertIsNotNone(fonts.find_candidate("hack"))
        self.assertIsNone(fonts.find_candidate("nope"))

    def test_licences_are_declared(self):
        for entry in fonts.catalog():
            with self.subTest(font=entry.key):
                self.assertTrue(entry.licence)


class FontListTests(unittest.TestCase):
    def test_split_trims_and_drops_empties(self):
        self.assertEqual(
            fonts.split_font_list("A, B ,, C"), ["A", "B", "C"]
        )

    def test_split_of_empty(self):
        self.assertEqual(fonts.split_font_list(""), [])
        self.assertEqual(fonts.split_font_list(None), [])

    def test_join_round_trip(self):
        families = ["JetBrains Mono", "Noto Sans Mono CJK SC"]
        self.assertEqual(fonts.split_font_list(fonts.join_font_list(families)), families)

    def test_join_skips_blanks(self):
        self.assertEqual(fonts.join_font_list(["A", "", "B"]), "A, B")

    def test_font_dir_honours_override(self):
        previous = os.environ.get(fonts.FONT_DIR_ENV)
        os.environ[fonts.FONT_DIR_ENV] = "/tmp/vela-font-dir-test"
        try:
            self.assertEqual(fonts.font_dir(), "/tmp/vela-font-dir-test")
        finally:
            if previous is None:
                os.environ.pop(fonts.FONT_DIR_ENV, None)
            else:
                os.environ[fonts.FONT_DIR_ENV] = previous

    def test_default_font_dir_is_under_local_share(self):
        previous = os.environ.pop(fonts.FONT_DIR_ENV, None)
        try:
            self.assertTrue(fonts.font_dir().endswith(os.path.join("share", "fonts")))
        finally:
            if previous is not None:
                os.environ[fonts.FONT_DIR_ENV] = previous


class SystemFontTests(unittest.TestCase):
    """Discovery against the real font set (needs Pango, no network)."""

    def test_monospace_families_are_sorted_and_unique(self):
        families = fonts.monospace_families()
        if not families:
            self.skipTest("Pango not available")
        self.assertEqual(families, sorted(families, key=lambda n: n.lower()))
        self.assertEqual(len(families), len(set(families)))

    def test_known_monospace_font_is_found(self):
        families = fonts.monospace_families()
        if not families:
            self.skipTest("Pango not available")
        # At least one of these ships with Ubuntu.
        self.assertTrue(
            any("Mono" in name for name in families), families
        )

    def test_is_installed_agrees_with_the_list(self):
        families = fonts.monospace_families()
        if not families:
            self.skipTest("Pango not available")
        self.assertTrue(fonts.is_installed(families[0]))
        self.assertFalse(fonts.is_installed("Definitely Not A Font 12345"))


if __name__ == "__main__":
    unittest.main()
