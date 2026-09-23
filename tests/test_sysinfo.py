"""sysinfo sampling: /proc parsing, counter differencing and formatting.

Every test drives the pure functions or injects fake /proc contents, so the
suite is deterministic and needs no particular machine load.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import sysinfo  # noqa: E402

CPU_STAT_A = """cpu  100 0 50 1000 100 0 0 0 0 0
cpu0 50 0 25 500 50 0 0 0 0 0
cpu1 50 0 25 500 50 0 0 0 0 0
intr 12345
ctxt 67890
"""

CPU_STAT_B = """cpu  200 0 100 1100 100 0 0 0 0 0
cpu0 100 0 50 550 50 0 0 0 0 0
cpu1 100 0 50 550 50 0 0 0 0 0
"""

MEMINFO = """MemTotal:       16065196 kB
MemFree:          333592 kB
MemAvailable:    2916608 kB
Buffers:          100000 kB
Cached:          1000000 kB
SwapTotal:       2097152 kB
SwapFree:        1048576 kB
"""

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  eth0: 200000 200 0 0 0 0 0 0 100000 100 0 0 0 0 0 0
"""


class CpuParsingTests(unittest.TestCase):
    def test_parses_overall_and_per_core(self):
        rows = sysinfo.parse_cpu_stat(CPU_STAT_A)
        labels = [label for label, _idle, _total in rows]
        self.assertEqual(labels, ["cpu", "cpu0", "cpu1"])
        _label, idle, total = rows[0]
        self.assertEqual(idle, 1100)  # idle + iowait
        self.assertEqual(total, 100 + 50 + 1000 + 100)

    def test_ignores_non_cpu_lines(self):
        rows = sysinfo.parse_cpu_stat(CPU_STAT_A)
        self.assertEqual(len(rows), 3)

    def test_percentages_from_deltas(self):
        before = sysinfo.parse_cpu_stat(CPU_STAT_A)
        after = sysinfo.parse_cpu_stat(CPU_STAT_B)
        percentages = sysinfo.cpu_percentages(before, after)
        # cpu: total 1250 -> 1500 (delta 250), idle 1100 -> 1200 (delta 100),
        # so 150/250 of the delta was busy.
        self.assertAlmostEqual(percentages["cpu"], 60.0, places=3)
        self.assertAlmostEqual(percentages["cpu0"], 60.0, places=3)

    def test_first_sample_has_no_baseline(self):
        after = sysinfo.parse_cpu_stat(CPU_STAT_B)
        self.assertEqual(sysinfo.cpu_percentages([], after), {})

    def test_counter_reset_reads_as_zero(self):
        before = sysinfo.parse_cpu_stat(CPU_STAT_B)
        after = sysinfo.parse_cpu_stat(CPU_STAT_A)
        percentages = sysinfo.cpu_percentages(before, after)
        self.assertEqual(percentages["cpu"], 0.0)

    def test_new_core_without_baseline_is_skipped(self):
        before = [("cpu", 1, 2)]
        after = [("cpu", 2, 4), ("cpu9", 1, 2)]
        self.assertEqual(list(sysinfo.cpu_percentages(before, after)), ["cpu"])

    def test_garbage_line_is_skipped(self):
        rows = sysinfo.parse_cpu_stat("cpu  a b c\ncpu0 1 1 1 1 1 1 1 1\n")
        self.assertEqual([label for label, _i, _t in rows], ["cpu0"])


class MemoryTests(unittest.TestCase):
    def test_kibibytes_become_bytes(self):
        values = sysinfo.parse_meminfo(MEMINFO)
        self.assertEqual(values["MemTotal"], 16065196 * 1024)
        self.assertEqual(values["SwapFree"], 1048576 * 1024)

    def test_used_follows_available(self):
        usage = sysinfo.memory_usage(sysinfo.parse_meminfo(MEMINFO))
        self.assertEqual(usage.total, 16065196 * 1024)
        self.assertEqual(usage.used, (16065196 - 2916608) * 1024)
        self.assertAlmostEqual(
            usage.percent, 100 * (16065196 - 2916608) / 16065196, places=4
        )
        self.assertEqual(usage.swap_used, 1048576 * 1024)
        self.assertAlmostEqual(usage.swap_percent, 50.0, places=4)

    def test_missing_available_falls_back_to_free(self):
        usage = sysinfo.memory_usage({"MemTotal": 100, "MemFree": 40})
        self.assertEqual(usage.used, 60)

    def test_zero_total_does_not_divide_by_zero(self):
        usage = sysinfo.memory_usage({})
        self.assertEqual((usage.total, usage.percent), (0, 0.0))


class NetworkTests(unittest.TestCase):
    def test_parses_interfaces(self):
        parsed = sysinfo.parse_net_dev(NET_DEV)
        self.assertEqual(parsed["eth0"], (200000, 100000))
        self.assertEqual(parsed["lo"], (1000, 1000))

    def test_rates_from_deltas(self):
        before = {"eth0": (0, 0)}
        after = {"eth0": (2048, 4096)}
        rates = sysinfo.network_rates(before, after, 2.0)
        self.assertEqual(len(rates), 1)
        self.assertAlmostEqual(rates[0].rx_rate, 1024.0)
        self.assertAlmostEqual(rates[0].tx_rate, 2048.0)

    def test_loopback_is_hidden_by_default(self):
        after = {"lo": (10, 10), "eth0": (0, 0)}
        names = [rate.name for rate in sysinfo.network_rates({}, after, 1.0)]
        self.assertEqual(names, ["eth0"])
        names = [
            rate.name
            for rate in sysinfo.network_rates({}, after, 1.0, skip_loopback=False)
        ]
        # Without a baseline every rate is zero, so the alphabetical tie-break
        # order is what remains.
        self.assertEqual(names, ["eth0", "lo"])

    def test_new_interface_reports_zero_not_full_totals(self):
        rates = sysinfo.network_rates({}, {"eth0": (1_000_000, 0)}, 1.0)
        self.assertEqual(rates[0].rx_rate, 0.0)
        self.assertEqual(rates[0].rx_total, 1_000_000)

    def test_counter_reset_reads_as_zero(self):
        rates = sysinfo.network_rates({"eth0": (5000, 5000)}, {"eth0": (10, 10)}, 1.0)
        self.assertEqual((rates[0].rx_rate, rates[0].tx_rate), (0.0, 0.0))

    def test_sorted_by_traffic(self):
        after = {"a": (100, 0), "b": (9000, 0)}
        rates = sysinfo.network_rates({"a": (0, 0), "b": (0, 0)}, after, 1.0)
        self.assertEqual([rate.name for rate in rates], ["b", "a"])

    def test_zero_elapsed_does_not_divide_by_zero(self):
        rates = sysinfo.network_rates({"eth0": (0, 0)}, {"eth0": (100, 0)}, 0.0)
        self.assertTrue(rates[0].rx_rate >= 0)


class LoadAndUptimeTests(unittest.TestCase):
    def test_loadavg(self):
        self.assertEqual(
            sysinfo.parse_loadavg("0.5 1.25 2.0 1/500 1234"), (0.5, 1.25, 2.0)
        )

    def test_loadavg_garbage(self):
        self.assertEqual(sysinfo.parse_loadavg("nonsense"), (0.0, 0.0, 0.0))

    def test_uptime(self):
        self.assertEqual(sysinfo.parse_uptime("12345.67 99999.00"), 12345.67)

    def test_uptime_garbage(self):
        self.assertEqual(sysinfo.parse_uptime(""), 0.0)


class ProcessTests(unittest.TestCase):
    STAT = (
        "1234 (my process) S 1 1234 1234 0 -1 4194560 100 0 0 0 "
        "50 25 0 0 20 0 8 0 100000 12345678 300 18446744073709551615 "
        "0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0\n"
    )

    def test_parses_name_with_spaces(self):
        parsed = sysinfo.parse_pid_stat(self.STAT)
        self.assertEqual(parsed["name"], "my process")
        self.assertEqual(parsed["utime"], 50)
        self.assertEqual(parsed["stime"], 25)
        self.assertEqual(parsed["threads"], 8)
        self.assertEqual(parsed["starttime"], 100000)

    def test_rss_and_cpu(self):
        usage = sysinfo.process_usage(
            1234,
            self.STAT,
            "1000 300 0 0 0 0 0",
            uptime=2000.0,
            clock_ticks=100.0,
            page_size=4096,
        )
        self.assertTrue(usage.available)
        self.assertEqual(usage.rss, 300 * 4096)
        self.assertEqual(usage.threads, 8)
        # CPU time 0.75s over (2000 - 1000)s of age -> ~0.075%
        self.assertAlmostEqual(usage.cpu_percent, 0.075, places=3)

    def test_short_stat_is_unavailable(self):
        usage = sysinfo.process_usage(1, "1 (x) S 1", "0 0", 10.0)
        self.assertFalse(usage.available)

    def test_process_older_than_uptime_is_safe(self):
        usage = sysinfo.process_usage(1234, self.STAT, "0 10", uptime=1.0)
        self.assertTrue(usage.available)
        self.assertGreaterEqual(usage.cpu_percent, 0.0)

    def test_missing_pid(self):
        self.assertFalse(sysinfo.Sampler().process(0).available)


class DiskTests(unittest.TestCase):
    class Stats:
        f_blocks = 1000
        f_bfree = 400
        f_bavail = 300
        f_frsize = 4096
        f_fsid = 42

    def test_percent_uses_available_not_free(self):
        disks = sysinfo.disk_usage(["/"], statvfs=lambda _p: self.Stats())
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].total, 1000 * 4096)
        self.assertEqual(disks[0].free, 300 * 4096)
        self.assertEqual(disks[0].used, 600 * 4096)
        # used / (used + free) = 600 / 900
        self.assertAlmostEqual(disks[0].percent, 100 * 600 / 900, places=4)

    def test_duplicate_mounts_collapse(self):
        disks = sysinfo.disk_usage(
            ["/", os.path.expanduser("~")], statvfs=lambda _p: self.Stats()
        )
        self.assertEqual(len(disks), 1)

    def test_missing_path_is_skipped(self):
        disks = sysinfo.disk_usage(
            ["/definitely/not/here"], statvfs=lambda _p: self.Stats()
        )
        self.assertEqual(disks, [])


class FormattingTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(sysinfo.format_bytes(0), "0 B")
        self.assertEqual(sysinfo.format_bytes(1536), "1.5 KiB")
        self.assertEqual(sysinfo.format_bytes(1024 ** 3), "1.0 GiB")

    def test_rate(self):
        self.assertEqual(sysinfo.format_rate(2048), "2.0 KiB/s")

    def test_percent_clamped(self):
        self.assertEqual(sysinfo.format_percent(150), "100.0%")
        self.assertEqual(sysinfo.format_percent(-5), "0.0%")

    def test_duration(self):
        self.assertEqual(sysinfo.format_duration(90), "1 分")
        self.assertEqual(sysinfo.format_duration(3700), "1 小时 1 分")
        self.assertIn("天", sysinfo.format_duration(90000))

    def test_sparkline_points(self):
        points = sysinfo.sparkline_points([0, 50, 100], 100, 40)
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0], (0.0, 40.0))
        self.assertEqual(points[-1][0], 100.0)
        self.assertEqual(points[-1][1], 0.0)

    def test_sparkline_empty(self):
        self.assertEqual(sysinfo.sparkline_points([], 100, 40), [])
        self.assertEqual(sysinfo.sparkline_points([1], 0, 40), [])

    def test_core_sorting_is_numeric(self):
        cores = {"cpu10": 1.0, "cpu2": 1.0, "cpu1": 1.0}
        ordered = [label for label, _value in sorted(cores.items(), key=sysinfo._core_sort_key)]
        self.assertEqual(ordered, ["cpu1", "cpu2", "cpu10"])

    def test_core_sorting_puts_unnumbered_last(self):
        cores = {"weird": 1.0, "cpu3": 1.0}
        ordered = [label for label, _value in sorted(cores.items(), key=sysinfo._core_sort_key)]
        self.assertEqual(ordered, ["cpu3", "weird"])


class SamplerTests(unittest.TestCase):
    def make_reader(self, files, counter=None):
        def reader(path):
            if path not in files:
                raise FileNotFoundError(2, "No such file or directory", path)
            if callable(files[path]):
                return files[path]()
            return files[path]

        return reader

    def test_first_sample_then_delta(self):
        state = {"cpu": CPU_STAT_A, "net": NET_DEV}
        clock = {"value": 100.0}
        sampler = sysinfo.Sampler(
            reader=self.make_reader(
                {
                    "/proc/stat": lambda: state["cpu"],
                    "/proc/meminfo": MEMINFO,
                    "/proc/net/dev": lambda: state["net"],
                    "/proc/loadavg": "1.0 2.0 3.0 1/10 99",
                    "/proc/uptime": "5000.0 0.0",
                }
            ),
            clock=lambda: clock["value"],
            disk_paths=[],
        )
        first = sampler.sample()
        self.assertEqual(first.cpu_percent, 0.0)
        self.assertEqual(first.core_count, 0)
        self.assertEqual(first.memory.total, 16065196 * 1024)
        self.assertEqual(first.load, (1.0, 2.0, 3.0))
        self.assertEqual(first.uptime, 5000.0)

        state["cpu"] = CPU_STAT_B
        clock["value"] = 102.0
        state["net"] = NET_DEV.replace("200000", "204096").replace(
            "100000", "104096"
        )
        second = sampler.sample()
        self.assertAlmostEqual(second.cpu_percent, 60.0, places=3)
        self.assertEqual(second.core_count, 2)
        eth = [rate for rate in second.network if rate.name == "eth0"][0]
        self.assertAlmostEqual(eth.rx_rate, 2048.0)
        self.assertAlmostEqual(eth.tx_rate, 2048.0)

    def test_missing_files_are_reported_but_not_fatal(self):
        sampler = sysinfo.Sampler(
            reader=self.make_reader({"proc/stat": CPU_STAT_A}),
            disk_paths=[],
        )
        sampler._read = self.make_reader({})
        snapshot = sampler.sample()
        self.assertFalse(snapshot.ok)
        self.assertTrue(snapshot.errors)
        # Unavailable metrics stay at their neutral defaults.
        self.assertEqual(snapshot.memory.total, 0)
        self.assertEqual(snapshot.cpu_percent, 0.0)

    def test_process_lookup_uses_reader(self):
        files = {
            "/proc/1234/stat": ProcessTests.STAT,
            "/proc/1234/statm": "1000 300 0 0 0 0 0",
            "/proc/uptime": "2000.0 0.0",
        }
        sampler = sysinfo.Sampler(reader=self.make_reader(files))
        usage = sampler.process(1234)
        self.assertTrue(usage.available)
        self.assertEqual(usage.pid, 1234)
        self.assertEqual(usage.rss, 300 * 4096)
        self.assertFalse(sampler.process(9999).available)


class RealSystemTests(unittest.TestCase):
    """A light sanity check against the real /proc of the test machine."""

    def test_real_sample_is_populated(self):
        sampler = sysinfo.Sampler()
        first = sampler.sample()
        self.assertGreater(first.memory.total, 0)
        self.assertGreater(first.process_count, 0)
        self.assertTrue(first.kernel)
        self.assertTrue(first.hostname)
        # Per-core usage needs two samples; the first one only establishes the
        # baseline counters.
        self.assertEqual(first.core_count, 0)
        second = sampler.sample()
        self.assertGreater(second.core_count, 0)
        self.assertGreaterEqual(second.cpu_percent, 0.0)
        self.assertLessEqual(second.cpu_percent, 100.0)
        for value in second.cpu_cores.values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_real_disk_usage(self):
        disks = sysinfo.disk_usage(["/"])
        self.assertEqual(len(disks), 1)
        self.assertGreater(disks[0].total, 0)
        self.assertGreaterEqual(disks[0].percent, 0.0)
        self.assertLessEqual(disks[0].percent, 100.0)

    def test_own_process_is_readable(self):
        usage = sysinfo.Sampler().process(os.getpid())
        self.assertTrue(usage.available)
        self.assertGreater(usage.rss, 0)
        self.assertGreater(usage.threads, 0)


if __name__ == "__main__":
    unittest.main()
