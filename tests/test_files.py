"""File classification, dispatch and safe read/write."""

import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import files  # noqa: E402


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = self._temp.name

    def tearDown(self):
        self._temp.cleanup()

    def write(self, name, content="hello", mode=None):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def write_bytes(self, name, payload):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path


class ClassifyTests(WorkspaceTestCase):
    def test_text_by_extension(self):
        for name in ("a.py", "b.md", "c.toml", "d.json", "e.log", "f.sh"):
            target = files.classify(self.write(name))
            self.assertEqual(target.kind, files.KIND_TEXT, name)
            self.assertTrue(target.exists)
            self.assertTrue(target.readable)

    def test_text_by_filename_without_extension(self):
        for name in ("README", "Makefile", "LICENSE"):
            self.assertEqual(files.classify(self.write(name)).kind, files.KIND_TEXT)

    def test_text_by_content_sniff(self):
        target = files.classify(self.write("mystery", "plain words\nmore words\n"))
        self.assertEqual(target.kind, files.KIND_TEXT)

    def test_binary_with_unknown_extension_is_not_text(self):
        payload = bytes(range(256)) * 4
        target = files.classify(self.write_bytes("blob.bin", payload))
        self.assertNotEqual(target.kind, files.KIND_TEXT)

    def test_image_extensions(self):
        for name in ("a.png", "b.jpg", "c.jpeg", "d.gif", "e.svg", "f.webp"):
            self.assertEqual(files.classify(self.write_bytes(name, b"x")).kind,
                             files.KIND_IMAGE, name)

    def test_directory(self):
        os.makedirs(os.path.join(self.root, "sub"))
        target = files.classify(os.path.join(self.root, "sub"))
        self.assertEqual(target.kind, files.KIND_DIRECTORY)
        self.assertFalse(target.openable_internally)

    def test_executable_without_known_extension(self):
        payload = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
        path = self.write_bytes("runner", payload)
        os.chmod(path, 0o755)
        self.assertEqual(files.classify(path).kind, files.KIND_EXECUTABLE)

    def test_missing_path(self):
        target = files.classify(os.path.join(self.root, "nope.txt"))
        self.assertEqual(target.kind, files.KIND_MISSING)
        self.assertFalse(target.exists)
        self.assertIn("不存在", target.reason)

    def test_empty_path(self):
        target = files.classify("")
        self.assertEqual(target.kind, files.KIND_MISSING)
        self.assertIn("空", target.reason)

    def test_tilde_and_relative_are_expanded(self):
        target = files.classify("~")
        self.assertTrue(os.path.isabs(target.path))
        self.assertEqual(target.kind, files.KIND_DIRECTORY)
        relative = files.classify(".")
        self.assertTrue(os.path.isabs(relative.path))

    def test_quotes_are_stripped(self):
        path = self.write("quoted.txt")
        self.assertEqual(files.classify(f'"{path}"').path, path)
        self.assertEqual(files.classify(f"'{path}'").path, path)

    def test_oversized_text_falls_back_to_desktop(self):
        path = self.write("big.txt", "x" * 64)
        target = files.classify(path)
        self.assertEqual(target.kind, files.KIND_TEXT)
        original = files.MAX_TEXT_BYTES
        try:
            files.MAX_TEXT_BYTES = 8
            target = files.classify(path)
            self.assertEqual(target.kind, files.KIND_OTHER)
            self.assertIn("过大", target.reason)
        finally:
            files.MAX_TEXT_BYTES = original


class DispatchTests(WorkspaceTestCase):
    def setUp(self):
        super().setUp()
        # Never launch real desktop applications from a test run.
        self._original_popen = files.subprocess.Popen
        self.launched = []
        files.subprocess.Popen = self._fake_popen

    def tearDown(self):
        files.subprocess.Popen = self._original_popen
        super().tearDown()

    def _fake_popen(self, command, **_kwargs):
        self.launched.append(list(command))
        return object()

    def test_desktop_commands_prefer_xdg_open(self):
        commands = files._desktop_commands("/tmp/file.txt")
        self.assertEqual(commands[0][0], "xdg-open")

    def test_directory_commands_include_file_manager(self):
        commands = files._desktop_commands(self.root)
        names = [command[0] for command in commands]
        self.assertIn("nautilus", names)
        self.assertLess(names.index("xdg-open"), names.index("nautilus"))

    def test_open_with_desktop_returns_bool(self):
        self.assertTrue(files.open_with_desktop(self.root))
        self.assertTrue(self.launched)

    def test_open_with_desktop_falls_through_missing_handlers(self):
        original = files.shutil.which
        try:
            files.shutil.which = lambda name: None if name == "xdg-open" else original(name)
            self.assertTrue(files.open_with_desktop(self.root))
        finally:
            files.shutil.which = original
        self.assertEqual(self.launched[0][0], "nautilus")

    def test_missing_handler_is_reported(self):
        original = files.shutil.which
        try:
            files.shutil.which = lambda _name: None
            self.assertFalse(files.open_with_desktop(self.root))
        finally:
            files.shutil.which = original
        self.assertEqual(self.launched, [])

    def test_reveal_uses_directory_of_file(self):
        path = self.write("a/b.txt")
        original_which = files.shutil.which
        try:
            files.shutil.which = lambda name: "/usr/bin/" + name
            self.assertTrue(files.reveal_in_file_manager(path))
        finally:
            files.shutil.which = original_which
        self.assertEqual(self.launched[-1], ["nautilus", "--select", path])

    def test_reveal_falls_back_to_desktop_handler(self):
        path = self.write("a/b.txt")
        original_which = files.shutil.which
        try:
            files.shutil.which = lambda name: None if name == "nautilus" else "/usr/bin/" + name
            self.assertTrue(files.reveal_in_file_manager(path))
        finally:
            files.shutil.which = original_which
        self.assertEqual(self.launched[-1][0], "xdg-open")
        self.assertEqual(self.launched[-1][1], os.path.dirname(path))


class ReadWriteTests(WorkspaceTestCase):
    def test_read_text(self):
        path = self.write("a.txt", "内容 content\n")
        self.assertEqual(files.read_text(path), "内容 content\n")

    def test_read_text_refuses_large_file(self):
        path = self.write("big.txt", "x" * 100)
        with self.assertRaises(ValueError):
            files.read_text(path, max_bytes=10)

    def test_read_text_replaces_invalid_utf8(self):
        path = self.write_bytes("bad.txt", b"ok \xff\xfe end")
        self.assertIn("ok", files.read_text(path))

    def test_write_text_is_atomic(self):
        path = self.write("a.txt", "old")
        files.write_text(path, "new")
        self.assertEqual(files.read_text(path), "new")
        leftovers = [name for name in os.listdir(self.root) if "vela-tmp" in name]
        self.assertEqual(leftovers, [])

    def test_write_text_creates_no_partial_on_error(self):
        path = os.path.join(self.root, "sub", "a.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        files.write_text(path, "hello")
        self.assertEqual(files.read_text(path), "hello")

    def test_write_text_preserves_permissions(self):
        path = self.write("a.txt", "old", mode=0o600)
        files.write_text(path, "new")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class CandidatePathTests(WorkspaceTestCase):
    def test_finds_existing_paths_in_output(self):
        path = self.write("out.txt")
        text = f"ls: wrote {path} successfully"
        self.assertIn(path, files.candidate_paths(text))

    def test_ignores_urls_and_words(self):
        text = "visit https://example.com/a.txt or just some words"
        self.assertEqual(files.candidate_paths(text), [])

    def test_ignores_missing_paths(self):
        self.assertEqual(files.candidate_paths("./does-not-exist-xyz"), [])

    def test_strips_surrounding_punctuation(self):
        path = self.write("note.md")
        self.assertIn(path, files.candidate_paths(f"see ({path}), ok"))

    def test_relative_path_resolution(self):
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            self.write("here.txt")
            self.assertIn(
                os.path.join(self.root, "here.txt"),
                files.candidate_paths("open ./here.txt now"),
            )
        finally:
            os.chdir(previous)


class ShellQuoteTests(unittest.TestCase):
    def test_plain_path_untouched(self):
        self.assertEqual(files.shell_quote("/tmp/a-b_c.txt"), "/tmp/a-b_c.txt")

    def test_spaces_are_quoted(self):
        self.assertEqual(files.shell_quote("/tmp/a b.txt"), "'/tmp/a b.txt'")

    def test_single_quote_is_escaped(self):
        self.assertEqual(files.shell_quote("it's"), "'it'\\''s'")

    def test_empty(self):
        self.assertEqual(files.shell_quote(""), "''")


class TextSniffTests(WorkspaceTestCase):
    def test_utf8_text(self):
        path = self.write("u.txt", "中文文本\n第二行\n")
        self.assertTrue(files.looks_like_text(path))

    def test_nul_bytes_are_binary(self):
        path = self.write_bytes("b.bin", b"abc\x00def")
        self.assertFalse(files.looks_like_text(path))

    def test_empty_file_is_text(self):
        path = self.write("empty.txt", "")
        self.assertTrue(files.looks_like_text(path))

    def test_mostly_high_bytes_is_binary(self):
        path = self.write_bytes("noise.bin", bytes(range(128, 256)) * 8)
        self.assertFalse(files.looks_like_text(path))

    def test_truncated_multibyte_tail_is_still_text(self):
        payload = "中文".encode("utf-8")[:5]  # cut a 3-byte character in half
        path = self.write_bytes("cut.txt", payload)
        self.assertTrue(files.looks_like_text(path))

    def test_missing_file_is_not_text(self):
        self.assertFalse(files.looks_like_text(os.path.join(self.root, "nope")))


if __name__ == "__main__":
    unittest.main()
