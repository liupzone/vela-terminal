"""Directory listing model: sorting, formatting, navigation and error cases."""

import os
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import fsmodel  # noqa: E402


class ModeTests(unittest.TestCase):
    def test_directory(self):
        self.assertEqual(fsmodel.format_mode(0o755 | stat.S_IFDIR, True), "drwxr-xr-x")

    def test_regular_file(self):
        self.assertEqual(fsmodel.format_mode(0o644, False), "-rw-r--r--")

    def test_executable(self):
        self.assertEqual(fsmodel.format_mode(0o755, False), "-rwxr-xr-x")

    def test_no_permissions(self):
        self.assertEqual(fsmodel.format_mode(0o000, False), "----------")

    def test_symlink(self):
        self.assertEqual(
            fsmodel.format_mode(0o777 | stat.S_IFLNK, False).startswith("l"), True
        )

    def test_setuid_without_execute_is_uppercase(self):
        mode = stat.S_ISUID | 0o644
        self.assertEqual(fsmodel.format_mode(mode, False), "-rwSr--r--")

    def test_setuid_with_execute_is_lowercase(self):
        mode = stat.S_ISUID | 0o755
        self.assertEqual(fsmodel.format_mode(mode, False), "-rwsr-xr-x")

    def test_setgid(self):
        self.assertEqual(fsmodel.format_mode(stat.S_ISGID | 0o755, False), "-rwxr-sr-x")

    def test_sticky_bit(self):
        self.assertEqual(
            fsmodel.format_mode(stat.S_ISVTX | 0o777 | stat.S_IFDIR, True),
            "drwxrwxrwt",
        )

    def test_socket_and_fifo(self):
        self.assertTrue(fsmodel.format_mode(0o644 | stat.S_IFSOCK, False).startswith("s"))
        self.assertTrue(fsmodel.format_mode(0o644 | stat.S_IFIFO, False).startswith("p"))


class SizeTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(fsmodel.format_size(0), "0B")
        self.assertEqual(fsmodel.format_size(512), "512B")

    def test_kilobytes_and_up(self):
        self.assertEqual(fsmodel.format_size(1024), "1.0K")
        self.assertEqual(fsmodel.format_size(1536), "1.5K")
        self.assertEqual(fsmodel.format_size(1024 ** 2), "1.0M")
        self.assertEqual(fsmodel.format_size(1024 ** 3), "1.0G")

    def test_negative_is_blank(self):
        self.assertEqual(fsmodel.format_size(-1), "")


class TimeTests(unittest.TestCase):
    def test_recent_shows_time(self):
        now = time.time()
        text = fsmodel.format_time(now, now=now)
        self.assertNotIn(str(time.localtime(now).tm_year), text)

    def test_old_shows_year(self):
        now = time.time()
        old = now - 400 * 24 * 3600
        self.assertIn(str(time.localtime(old).tm_year), fsmodel.format_time(old, now=now))

    def test_zero_is_blank(self):
        self.assertEqual(fsmodel.format_time(0), "")


class NaturalSortTests(unittest.TestCase):
    def test_numbers_compare_numerically(self):
        names = ["file10", "file2", "file1"]
        self.assertEqual(
            sorted(names, key=fsmodel.natural_key), ["file1", "file2", "file10"]
        )

    def test_case_insensitive(self):
        self.assertEqual(
            sorted(["Banana", "apple"], key=fsmodel.natural_key), ["apple", "Banana"]
        )

    def test_directories_first(self):
        entries = [
            fsmodel.Entry(name="b.txt", path="/b.txt"),
            fsmodel.Entry(name="a", path="/a", is_dir=True),
            fsmodel.Entry(name="a.txt", path="/a.txt"),
        ]
        ordered = fsmodel.sort_entries(entries)
        self.assertEqual([item.name for item in ordered], ["a", "a.txt", "b.txt"])

    def test_files_only_when_disabled(self):
        entries = [
            fsmodel.Entry(name="z", path="/z", is_dir=True),
            fsmodel.Entry(name="a", path="/a"),
        ]
        ordered = fsmodel.sort_entries(entries, directories_first=False)
        self.assertEqual([item.name for item in ordered], ["a", "z"])


class PathTests(unittest.TestCase):
    def test_parent_of_root_is_empty(self):
        self.assertEqual(fsmodel.parent_path("/"), "")

    def test_parent_of_normal_path(self):
        self.assertEqual(fsmodel.parent_path("/a/b/c"), "/a/b")

    def test_breadcrumbs_from_root(self):
        crumbs = fsmodel.breadcrumbs("/usr/share/doc")
        self.assertEqual(crumbs[0], ("/", "/"))
        self.assertEqual([label for label, _path in crumbs], ["/", "usr", "share", "doc"])
        self.assertEqual(crumbs[-1][1], "/usr/share/doc")

    def test_breadcrumbs_of_root(self):
        self.assertEqual(fsmodel.breadcrumbs("/"), [("/", "/")])

    def test_home_is_shown_as_tilde(self):
        home = os.path.expanduser("~")
        crumbs = fsmodel.breadcrumbs(home)
        self.assertIn("~", [label for label, _path in crumbs])


class ReadDirectoryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = self._temp.name

    def tearDown(self):
        self._temp.cleanup()

    def touch(self, name, content=""):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def mkdir(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        return path

    def test_lists_files_and_directories(self):
        self.touch("a.txt", "hello")
        self.mkdir("sub")
        listing = fsmodel.read_directory(self.root)
        self.assertTrue(listing.ok)
        names = [entry.name for entry in listing.entries]
        self.assertIn("a.txt", names)
        self.assertIn("sub", names)

    def test_directories_come_first(self):
        self.touch("aaa.txt")
        self.mkdir("zzz")
        listing = fsmodel.read_directory(self.root)
        self.assertEqual(listing.entries[0].name, "zzz")
        self.assertTrue(listing.entries[0].is_dir)

    def test_hidden_files_are_filtered_by_default(self):
        self.touch(".hidden")
        self.touch("visible")
        listing = fsmodel.read_directory(self.root)
        names = [entry.name for entry in listing.entries]
        self.assertNotIn(".hidden", names)
        self.assertIn("visible", names)

    def test_hidden_files_shown_on_request(self):
        self.touch(".hidden")
        listing = fsmodel.read_directory(self.root, show_hidden=True)
        self.assertIn(".hidden", [entry.name for entry in listing.entries])

    def test_permissions_and_size_are_filled(self):
        self.touch("a.txt", "12345")
        listing = fsmodel.read_directory(self.root)
        entry = listing.entries[0]
        self.assertEqual(entry.permissions[0], "-")
        self.assertEqual(entry.size, 5)
        self.assertGreater(entry.mtime, 0)
        self.assertEqual(entry.size_text, "5B")

    def test_directory_size_text_is_blank(self):
        self.mkdir("d")
        entry = fsmodel.read_directory(self.root).entries[0]
        self.assertEqual(entry.size_text, "")

    def test_symlink_reports_target(self):
        target = self.touch("real.txt")
        link = os.path.join(self.root, "link.txt")
        os.symlink(target, link)
        listing = fsmodel.read_directory(self.root)
        entry = [item for item in listing.entries if item.name == "link.txt"][0]
        self.assertTrue(entry.is_link)
        self.assertIn("real.txt", entry.link_target)
        self.assertIn("→", entry.display_name)

    def test_broken_symlink_is_still_listed(self):
        os.symlink(os.path.join(self.root, "nope"), os.path.join(self.root, "broken"))
        listing = fsmodel.read_directory(self.root)
        names = [entry.name for entry in listing.entries]
        self.assertIn("broken", names)

    def test_directory_symlink_resolves_for_navigation(self):
        real = self.mkdir("real")
        link = os.path.join(self.root, "link")
        os.symlink(real, link)
        listing = fsmodel.read_directory(self.root)
        entry = [item for item in listing.entries if item.name == "link"][0]
        self.assertTrue(entry.is_dir)
        self.assertEqual(fsmodel.resolve_entry_target(entry), real)

    def test_missing_directory(self):
        listing = fsmodel.read_directory(os.path.join(self.root, "nope"))
        self.assertFalse(listing.ok)
        self.assertIn("不存在", listing.error)
        self.assertEqual(listing.entries, [])

    def test_path_that_is_a_file(self):
        path = self.touch("a.txt")
        listing = fsmodel.read_directory(path)
        self.assertFalse(listing.ok)
        self.assertIn("不是目录", listing.error)

    def test_empty_path(self):
        listing = fsmodel.read_directory("")
        self.assertFalse(listing.ok)
        self.assertIn("空", listing.error)

    def test_empty_directory(self):
        listing = fsmodel.read_directory(self.root)
        self.assertTrue(listing.ok)
        self.assertEqual(listing.entries, [])

    def test_truncation_is_reported(self):
        for index in range(10):
            self.touch(f"f{index:02d}.txt")
        listing = fsmodel.read_directory(self.root, max_entries=4)
        self.assertEqual(len(listing.entries), 4)
        self.assertEqual(listing.truncated, 6)
        self.assertEqual(listing.total, 10)
        self.assertIn("未显示", fsmodel.summarize(listing))

    def test_permission_error_is_reported(self):
        blocked = self.mkdir("blocked")
        os.chmod(blocked, 0o000)
        try:
            listing = fsmodel.read_directory(blocked)
            # Running as root can read anything, so accept either outcome but
            # never a crash.
            if not listing.ok:
                self.assertIn("权限", listing.error)
        finally:
            os.chmod(blocked, 0o755)

    def test_unreadable_file_is_flagged(self):
        path = self.touch("secret.txt")
        os.chmod(path, 0o000)
        try:
            listing = fsmodel.read_directory(self.root)
            entry = [item for item in listing.entries if item.name == "secret.txt"][0]
            if os.geteuid() != 0:
                self.assertFalse(entry.readable)
        finally:
            os.chmod(path, 0o644)

    def test_summary_counts(self):
        self.mkdir("a")
        self.mkdir("b")
        self.touch("c.txt")
        listing = fsmodel.read_directory(self.root)
        text = fsmodel.summarize(listing)
        self.assertIn("2 个文件夹", text)
        self.assertIn("1 个文件", text)

    def test_summary_shows_error(self):
        listing = fsmodel.read_directory(os.path.join(self.root, "nope"))
        self.assertIn("不存在", fsmodel.summarize(listing))

    def test_tilde_is_expanded(self):
        listing = fsmodel.read_directory("~")
        self.assertTrue(listing.path.startswith(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
