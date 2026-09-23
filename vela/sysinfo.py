"""System performance panel.

Everything here reads ``/proc`` and ``os.statvfs`` directly: Vela promises to run
on a stock Ubuntu 20.04 with no third-party packages, so pulling in ``psutil`` is
not an option.  The cost is that counter differencing is our job, which is why
the parsing and maths live in pure functions that tests can drive with fake
``/proc`` contents.
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import theme as theme_mod  # noqa: E402

try:  # pragma: no cover - platform constants
    CLOCK_TICKS = float(os.sysconf("SC_CLK_TCK"))
except (ValueError, OSError, AttributeError):  # pragma: no cover
    CLOCK_TICKS = 100.0

try:  # pragma: no cover - platform constants
    PAGE_SIZE = int(os.sysconf("SC_PAGE_SIZE"))
except (ValueError, OSError, AttributeError):  # pragma: no cover
    PAGE_SIZE = 4096

PROC_STAT = "/proc/stat"
PROC_MEMINFO = "/proc/meminfo"
PROC_NET_DEV = "/proc/net/dev"
PROC_UPTIME = "/proc/uptime"
PROC_LOADAVG = "/proc/loadavg"


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------
@dataclass
class MemoryUsage:
    total: int = 0
    used: int = 0
    available: int = 0
    percent: float = 0.0
    swap_total: int = 0
    swap_used: int = 0
    swap_percent: float = 0.0


@dataclass
class DiskUsage:
    mount: str
    total: int = 0
    used: int = 0
    free: int = 0
    percent: float = 0.0


@dataclass
class NetworkRate:
    name: str
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    rx_total: int = 0
    tx_total: int = 0


@dataclass
class ProcessUsage:
    pid: int = 0
    name: str = ""
    cpu_percent: float = 0.0
    rss: int = 0
    threads: int = 0
    available: bool = True


@dataclass
class Snapshot:
    cpu_percent: float = 0.0
    cpu_cores: Dict[str, float] = field(default_factory=dict)
    load: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    core_count: int = 0
    memory: MemoryUsage = field(default_factory=MemoryUsage)
    disks: List[DiskUsage] = field(default_factory=list)
    network: List[NetworkRate] = field(default_factory=list)
    uptime: float = 0.0
    kernel: str = ""
    hostname: str = ""
    process_count: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# parsing helpers (pure)
# ---------------------------------------------------------------------------
def parse_cpu_stat(text: str) -> List[Tuple[str, int, int]]:
    """``/proc/stat`` CPU lines -> ``[(label, idle, total), ...]``.

    ``idle`` includes ``iowait`` because that is how every mainstream monitor
    counts it; ``guest``/``guest_nice`` are already inside ``user``/``nice`` so
    they are deliberately not added again.
    """
    rows: List[Tuple[str, int, int]] = []
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        label = parts[0]
        try:
            values = [int(value) for value in parts[1:11]]
        except ValueError:
            continue
        while len(values) < 8:
            values.append(0)
        user, nice, system, idle, iowait, irq, softirq, steal = values[:8]
        idle_total = idle + iowait
        total = user + nice + system + idle + iowait + irq + softirq + steal
        rows.append((label, idle_total, total))
    return rows


def _percent(busy_delta: int, total_delta: int) -> float:
    """Busy share from counter deltas, tolerant of resets and bogus values."""
    if total_delta <= 0 or busy_delta < 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * busy_delta / total_delta))


def cpu_percentages(
    previous: Sequence[Tuple[str, int, int]],
    current: Sequence[Tuple[str, int, int]],
) -> Dict[str, float]:
    """Usage per CPU line, keyed by label (``"cpu"`` is the overall row)."""
    if not previous:
        return {}
    before = {label: (idle, total) for label, idle, total in previous}
    result: Dict[str, float] = {}
    for label, idle, total in current:
        old = before.get(label)
        if old is None:
            continue
        idle_delta = idle - old[0]
        total_delta = total - old[1]
        result[label] = _percent(total_delta - idle_delta, total_delta)
    return result


def parse_meminfo(text: str) -> Dict[str, int]:
    """``/proc/meminfo`` -> ``{key: bytes}`` (the file reports kibibytes)."""
    values: Dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        number = rest.strip().split(" ")[0]
        if not number.isdigit():
            continue
        values[key.strip()] = int(number) * 1024
    return values


def memory_usage(meminfo: Dict[str, int]) -> MemoryUsage:
    """Used memory follows ``free``: total minus available, not minus free."""
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    used = max(0, total - available)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    return MemoryUsage(
        total=total,
        used=used,
        available=available,
        percent=_ratio_percent(used, total),
        swap_total=swap_total,
        swap_used=swap_used,
        swap_percent=_ratio_percent(swap_used, swap_total),
    )


def _ratio_percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * part / whole))


def parse_net_dev(text: str) -> Dict[str, Tuple[int, int]]:
    """``/proc/net/dev`` -> ``{interface: (rx_bytes, tx_bytes)}``."""
    interfaces: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name.startswith("Inter-") or name.startswith("face"):
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            interfaces[name] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return interfaces


def network_rates(
    previous: Dict[str, Tuple[int, int]],
    current: Dict[str, Tuple[int, int]],
    elapsed: float,
    skip_loopback: bool = True,
) -> List[NetworkRate]:
    """Per-interface rates; a counter that went backwards reads as zero."""
    rates: List[NetworkRate] = []
    if elapsed <= 0:
        elapsed = 1.0
    for name in sorted(current):
        if skip_loopback and name == "lo":
            continue
        rx_total, tx_total = current[name]
        old = previous.get(name)
        if old is None:
            rx_rate = tx_rate = 0.0
        else:
            rx_rate = max(0.0, (rx_total - old[0]) / elapsed)
            tx_rate = max(0.0, (tx_total - old[1]) / elapsed)
        rates.append(
            NetworkRate(
                name=name,
                rx_rate=rx_rate,
                tx_rate=tx_rate,
                rx_total=rx_total,
                tx_total=tx_total,
            )
        )
    rates.sort(key=lambda item: -(item.rx_rate + item.tx_rate))
    return rates


def parse_loadavg(text: str) -> Tuple[float, float, float]:
    parts = text.split()
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (IndexError, ValueError):
        return (0.0, 0.0, 0.0)


def parse_uptime(text: str) -> float:
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return 0.0


def parse_pid_stat(text: str) -> Optional[Dict[str, object]]:
    """``/proc/<pid>/stat`` -> the fields we care about.

    The process name sits in parentheses and may contain spaces or parentheses,
    so the line is split from the right-hand side.
    """
    if ")" not in text or "(" not in text:
        return None
    head, _, tail = text.rpartition(")")
    name = head[head.find("(") + 1 :]
    fields = tail.split()
    if len(fields) < 22:
        return None
    try:
        return {
            "name": name,
            "state": fields[0],
            "utime": int(fields[11]),
            "stime": int(fields[12]),
            "threads": int(fields[17]),
            "starttime": int(fields[19]),
        }
    except (IndexError, ValueError):
        return None


def process_usage(
    pid: int,
    stat_text: str,
    statm_text: str,
    uptime: float,
    clock_ticks: float = CLOCK_TICKS,
    page_size: int = PAGE_SIZE,
) -> ProcessUsage:
    """CPU and RSS for one process.

    CPU is expressed as ``ps`` does it: total CPU time divided by process age, so
    the value is an average over the process lifetime rather than an instant.
    """
    parsed = parse_pid_stat(stat_text)
    if parsed is None:
        return ProcessUsage(pid=pid, available=False)
    rss = 0
    fields = statm_text.split()
    if len(fields) >= 2:
        try:
            rss = int(fields[1]) * page_size
        except ValueError:
            rss = 0
    started = float(parsed["starttime"]) / clock_ticks
    age = uptime - started
    cpu = 0.0
    if age > 0:
        cpu_time = (float(parsed["utime"]) + float(parsed["stime"])) / clock_ticks
        cpu = max(0.0, min(100.0 * max(1, os.cpu_count() or 1), 100.0 * cpu_time / age))
    return ProcessUsage(
        pid=pid,
        name=str(parsed["name"]),
        cpu_percent=cpu,
        rss=rss,
        threads=int(parsed["threads"]),
        available=True,
    )


def disk_usage(paths: Iterable[str], statvfs: Optional[Callable] = None) -> List[DiskUsage]:
    """Usage per distinct filesystem backing ``paths`` (duplicates collapsed)."""
    statvfs = statvfs or os.statvfs
    seen: set = set()
    result: List[DiskUsage] = []
    for path in paths:
        if not path:
            continue
        target = os.path.expanduser(path)
        if not os.path.exists(target):
            continue
        try:
            stats = statvfs(target)
        except OSError:
            continue
        device = getattr(stats, "f_fsid", None) or (stats.f_blocks, stats.f_bfree)
        if device in seen:
            continue
        seen.add(device)
        total = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        used = max(0, total - stats.f_bfree * stats.f_frsize)
        result.append(
            DiskUsage(
                mount=target,
                total=total,
                used=used,
                free=free,
                percent=_ratio_percent(used, used + free),
            )
        )
    return result


def count_processes(proc_dir: str = "/proc") -> int:
    try:
        return sum(1 for entry in os.listdir(proc_dir) if entry.isdigit())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# formatting helpers (pure)
# ---------------------------------------------------------------------------
_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def format_bytes(value: float, suffix: str = "") -> str:
    number = float(max(0.0, value))
    index = 0
    while number >= 1024 and index < len(_UNITS) - 1:
        number /= 1024
        index += 1
    if index == 0:
        text = f"{int(number)} {_UNITS[index]}"
    else:
        text = f"{number:.1f} {_UNITS[index]}"
    return text + suffix


def format_rate(bytes_per_second: float) -> str:
    return format_bytes(bytes_per_second, "/s")


def format_percent(value: float) -> str:
    return f"{max(0.0, min(100.0, value)):.1f}%"


def format_duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时 {minutes} 分"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


def sparkline_points(
    values: Sequence[float], width: float, height: float, maximum: float = 100.0
) -> List[Tuple[float, float]]:
    """Points for a CPU history sparkline; empty input yields no points."""
    if not values or width <= 0 or height <= 0:
        return []
    span = max(1.0, float(maximum))
    step = width / max(1, len(values) - 1) if len(values) > 1 else width
    points: List[Tuple[float, float]] = []
    for index, value in enumerate(values):
        clamped = max(0.0, min(span, float(value)))
        points.append((index * step, height - (clamped / span) * height))
    return points


# ---------------------------------------------------------------------------
# sampler
# ---------------------------------------------------------------------------
def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _safe_read(path: str) -> str:
    try:
        return _read_file(path)
    except OSError:
        return ""


class Sampler:
    """Collects one :class:`Snapshot`; keeps the previous counters for deltas."""

    def __init__(
        self,
        reader: Optional[Callable[[str], str]] = None,
        clock: Optional[Callable[[], float]] = None,
        disk_paths: Optional[Sequence[str]] = None,
        skip_loopback: bool = True,
        proc_dir: str = "/proc",
    ) -> None:
        self._read = reader or _read_file
        self._clock = clock or time.monotonic
        self._proc_dir = proc_dir
        self.disk_paths = list(disk_paths or ["/", os.path.expanduser("~")])
        self.skip_loopback = skip_loopback
        self._previous_cpu: List[Tuple[str, int, int]] = []
        self._previous_net: Dict[str, Tuple[int, int]] = {}
        self._previous_time: Optional[float] = None

    # -- individual readings --------------------------------------------
    def _read_optional(self, path: str, errors: List[str], label: str) -> Optional[str]:
        try:
            return self._read(path)
        except OSError as error:
            errors.append(f"{label} 不可用（{error.strerror or error}）")
            return None

    def sample(self) -> Snapshot:
        snapshot = Snapshot()
        errors = snapshot.errors

        cpu_text = self._read_optional(PROC_STAT, errors, "CPU")
        if cpu_text is not None:
            current = parse_cpu_stat(cpu_text)
            percentages = cpu_percentages(self._previous_cpu, current)
            snapshot.cpu_percent = percentages.get("cpu", 0.0)
            snapshot.cpu_cores = {
                label: value
                for label, value in percentages.items()
                if label != "cpu"
            }
            snapshot.core_count = len(snapshot.cpu_cores)
            self._previous_cpu = current

        load_text = self._read_optional(PROC_LOADAVG, errors, "负载")
        if load_text is not None:
            snapshot.load = parse_loadavg(load_text)

        mem_text = self._read_optional(PROC_MEMINFO, errors, "内存")
        if mem_text is not None:
            snapshot.memory = memory_usage(parse_meminfo(mem_text))

        net_text = self._read_optional(PROC_NET_DEV, errors, "网络")
        now = self._clock()
        if net_text is not None:
            current_net = parse_net_dev(net_text)
            elapsed = (
                now - self._previous_time
                if self._previous_time is not None
                else 0.0
            )
            snapshot.network = network_rates(
                self._previous_net, current_net, elapsed, self.skip_loopback
            )
            self._previous_net = current_net
        self._previous_time = now

        uptime_text = self._read_optional(PROC_UPTIME, errors, "运行时长")
        if uptime_text is not None:
            snapshot.uptime = parse_uptime(uptime_text)

        try:
            snapshot.disks = disk_usage(self.disk_paths)
        except OSError as error:  # pragma: no cover - defensive
            errors.append(f"磁盘不可用（{error}）")
        snapshot.process_count = count_processes(self._proc_dir)
        snapshot.kernel = os.uname().release
        snapshot.hostname = socket.gethostname()
        return snapshot

    # -- process detail --------------------------------------------------
    def process(self, pid: int) -> ProcessUsage:
        if pid <= 0:
            return ProcessUsage(pid=pid, available=False)
        try:
            stat_text = self._read(f"{self._proc_dir}/{pid}/stat")
            statm_text = self._read(f"{self._proc_dir}/{pid}/statm")
        except OSError:
            return ProcessUsage(pid=pid, available=False)
        try:
            uptime_text = self._read(PROC_UPTIME)
        except OSError:
            uptime_text = "0"
        return process_usage(pid, stat_text, statm_text, parse_uptime(uptime_text))


# ---------------------------------------------------------------------------
# panel UI
# ---------------------------------------------------------------------------
SPARK_SAMPLES = 60
# Safety bound for pre-created per-core meters (a machine reporting more cores
# than this shows only the first this many).
MAX_CORE_CELLS = 256
# Height of one row of per-core meters, in pixels.
CORE_ROW_HEIGHT = 18


class Sparkline(Gtk.DrawingArea):
    """Tiny CPU history chart drawn with cairo."""

    def __init__(self, height: int = 34) -> None:
        super().__init__()
        self.values: List[float] = []
        self.line_color = "#7aa2f7"
        self.fill_color = "#7aa2f7"
        self.set_size_request(-1, height)
        self.connect("draw", self._on_draw)

    def push(self, value: float) -> None:
        self.values.append(max(0.0, min(100.0, float(value))))
        del self.values[:-SPARK_SAMPLES]
        self.queue_draw()

    def set_colors(self, line: str, fill: str) -> None:
        self.line_color = line
        self.fill_color = fill
        self.queue_draw()

    def _on_draw(self, _widget, cr) -> bool:
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        points = sparkline_points(self.values, width, height)
        if len(points) < 2:
            return False
        red, green, blue = _rgb(self.fill_color)
        cr.move_to(points[0][0], height)
        for x, y in points:
            cr.line_to(x, y)
        cr.line_to(points[-1][0], height)
        cr.close_path()
        cr.set_source_rgba(red, green, blue, 0.22)
        cr.fill()
        red, green, blue = _rgb(self.line_color)
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.set_source_rgba(red, green, blue, 0.95)
        cr.set_line_width(1.6)
        cr.stroke()
        return False


class Meter(Gtk.Label):
    """A slim usage bar rendered as text.

    Deliberately not a ``Gtk.ProgressBar`` (it has a ~150px minimum width, which
    pushed the panel past its allocation) and not a ``Gtk.DrawingArea`` either:
    drawing areas added to an already-visible container were allocated 1px here,
    and no combination of ``queue_resize``/re-parenting fixed it.  A label has
    predictable sizing and needs no custom drawing.
    """

    #: Number of cells in the bar.
    CELLS = 12
    #: Cell characters, from empty to full.
    LEVELS = " ▏▎▍▌▋▊▉█"

    def __init__(self, cells: int = CELLS) -> None:
        super().__init__(label="")
        self.cells = max(1, int(cells))
        self.fraction = 0.0
        self.fill_color = "#7aa2f7"
        self.set_xalign(0.0)
        self.get_style_context().add_class("vela-meter-text")
        self._render()

    def set_fraction(self, fraction: float) -> None:
        value = max(0.0, min(1.0, float(fraction)))
        if abs(value - self.fraction) > 0.005:
            self.fraction = value
            self._render()

    def set_colors(self, _track: str, fill: str) -> None:
        if fill != self.fill_color:
            self.fill_color = fill
            self._render()

    def _render(self) -> None:
        # Partial eighth-blocks give a smooth-looking bar in a fixed width.
        eighths = int(round(self.fraction * self.cells * 8))
        full, remainder = divmod(eighths, 8)
        full = min(full, self.cells)
        text = "█" * full
        if full < self.cells and remainder:
            text += self.LEVELS[remainder]
        text += " " * max(0, self.cells - len(text))
        self.set_text(text)
        self.set_tooltip_text(f"{self.fraction * 100:.1f}%")
        # A stylesheet rule keyed on a CSS class cannot carry a per-widget
        # colour, so the markup is applied directly.
        self.set_markup(
            f'<span foreground="{self.fill_color}">{_escape_markup(text)}</span>'
        )


def _escape_markup(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _rgb(color: str) -> Tuple[float, float, float]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        return (0.5, 0.5, 0.5)
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


class SysinfoPanel(Gtk.Box):
    """Side panel showing live system metrics.

    Sampling only runs while the panel is visible so a hidden panel costs
    nothing; the process rows are refreshed from the active tab on demand.
    """

    def __init__(
        self,
        config,
        theme: theme_mod.Theme,
        pane_provider: Optional[Callable[[], List[Tuple[str, int]]]] = None,
        notify: Optional[Callable] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.config = config
        self.theme = theme
        self._pane_provider = pane_provider or (lambda: [])
        self._notify = notify or (lambda *_args, **_kwargs: None)
        self.sampler = Sampler(
            skip_loopback=bool(config.get("sysinfo.hide_loopback"))
        )
        self._timer = 0
        self.last_snapshot: Optional[Snapshot] = None
        # (normal, warning, critical) fill colours for the meters.
        self._meter_colors: Tuple[str, str, str] = (
            theme.accent,
            theme.palette.get("yellow", theme.accent),
            theme.palette.get("red", theme.accent),
        )
        self._rows: Dict[str, Gtk.Label] = {}
        self._bars: Dict[str, Meter] = {}
        self._core_box: Optional[Gtk.FlowBox] = None
        self._core_bars: Dict[str, Meter] = {}
        self._core_labels: Dict[str, Gtk.Label] = {}
        self._core_cells: Dict[str, Gtk.Box] = {}
        self._core_rows: List[Gtk.Box] = []
        self._process_box: Optional[Gtk.Box] = None
        self._spark: Optional[Sparkline] = None

        self.get_style_context().add_class("vela-sysinfo")
        self.set_size_request(300, -1)
        self._build()
        self.apply_theme(theme)

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="系统性能")
        title.set_xalign(0.0)
        title.get_style_context().add_class("vela-panel-title")
        header.pack_start(title, True, True, 0)
        self._updated = Gtk.Label(label="")
        self._updated.get_style_context().add_class("vela-dim")
        header.pack_end(self._updated, False, False, 0)
        self.pack_start(header, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_border_width(10)

        self._spark = Sparkline()
        content.pack_start(self._spark, False, False, 0)

        self._add_section(content, "CPU")
        self._add_bar(content, "cpu", "总体")
        # Per-core meters are laid out as plain rows of two cells, and every cell
        # is created *now*, before the panel is first shown: GTK does not
        # re-allocate a container whose children are added while it is already on
        # screen (the container kept a 1px height and the rows stayed invisible,
        # no matter which resize call was used).  Extra cells are hidden until
        # the real core count is known.
        self._core_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # os.cpu_count() matches the number of cpuN lines in /proc/stat, and it
        # is known before the panel is shown, so the exact number of cells can be
        # created up front (see the note above about adding children later).
        core_total = max(1, min(MAX_CORE_CELLS, os.cpu_count() or 1))
        self._build_core_cells([f"cpu{index}" for index in range(core_total)])
        # Not expanding: the content column is taller than the viewport, so GTK
        # compresses expanding children and the core rows would be squeezed.
        content.pack_start(self._core_box, False, False, 0)
        self._add_row(content, "load", "负载")

        self._add_section(content, "内存")
        self._add_bar(content, "memory", "内存")
        self._add_bar(content, "swap", "交换分区")

        self._add_section(content, "磁盘")
        self._disk_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.pack_start(self._disk_box, False, False, 0)

        self._add_section(content, "网络")
        self._network_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.pack_start(self._network_box, False, False, 0)

        self._add_section(content, "终端进程")
        self._process_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.pack_start(self._process_box, False, False, 0)

        self._add_section(content, "系统")
        self._add_row(content, "host", "主机")
        self._add_row(content, "kernel", "内核")
        self._add_row(content, "uptime", "运行时长")
        self._add_row(content, "processes", "进程数")
        self._add_row(content, "vela", "Vela 自身")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(content)
        self.pack_start(scroller, True, True, 0)

    def _add_section(self, parent: Gtk.Box, title: str) -> None:
        label = Gtk.Label(label=title)
        label.set_xalign(0.0)
        label.get_style_context().add_class("vela-panel-section")
        parent.pack_start(label, False, False, 0)

    def _add_row(self, parent: Gtk.Box, key: str, label: str) -> Gtk.Label:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=label)
        name.set_xalign(0.0)
        name.get_style_context().add_class("vela-dim")
        value = Gtk.Label(label="—")
        value.set_xalign(1.0)
        value.set_ellipsize(3)
        value.set_selectable(True)
        # Long readouts (memory, disk) must not force the panel wider than the
        # width the user configured.
        value.set_max_width_chars(28)
        box.pack_start(name, False, False, 0)
        box.pack_end(value, True, True, 0)
        parent.pack_start(box, False, False, 0)
        self._rows[key] = value
        return value

    def _add_bar(self, parent: Gtk.Box, key: str, label: str) -> Meter:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=label)
        name.set_xalign(0.0)
        name.get_style_context().add_class("vela-dim")
        value = Gtk.Label(label="—")
        value.set_xalign(1.0)
        value.set_ellipsize(3)
        value.set_max_width_chars(26)
        box.pack_start(name, False, False, 0)
        box.pack_end(value, True, True, 0)
        bar = Meter()
        parent.pack_start(box, False, False, 0)
        parent.pack_start(bar, False, False, 0)
        self._rows[key] = value
        self._bars[key] = bar
        return bar

    # -- theming ---------------------------------------------------------
    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self._meter_colors = (
            theme.accent,
            theme.palette.get("yellow", theme.accent),
            theme.palette.get("red", theme.accent),
        )
        if self._spark is not None:
            self._spark.set_colors(
                theme.accent, theme.palette.get("cyan", theme.accent)
            )
        # Repaint existing meters with the new palette.
        for meter in list(self._bars.values()) + list(self._core_bars.values()):
            meter.set_colors("", self._meter_colors[0])
        if self.last_snapshot is not None:
            self._apply(self.last_snapshot)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        # Build the core cells only after the panel has been laid out once.
        # Filling a Box that is already on screen leaves GTK with a stale size
        # request (the container kept a 1px allocation and the rows were
        # invisible), so the first refresh is deferred by an idle callback.
        GLib.idle_add(self._first_refresh)
        if not self._timer:
            interval = max(500, int(float(self.config.get("sysinfo.interval")) * 1000))
            self._timer = GLib.timeout_add(interval, self._tick)

    def _first_refresh(self) -> bool:
        if self.get_visible():
            self.refresh()
        return False

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self) -> bool:
        if not self.get_visible():
            return True
        self.refresh()
        return True

    def refresh(self) -> None:
        snapshot = self.sampler.sample()
        self.last_snapshot = snapshot
        self._apply(snapshot)
        self._refresh_processes()
        self._notify_status(snapshot)
        if snapshot.errors:
            self._updated.set_text("部分指标不可用")
            self._updated.set_tooltip_text("\n".join(snapshot.errors))
        else:
            self._updated.set_text("实时")
            self._updated.set_tooltip_text("")

    def _notify_status(self, snapshot: Snapshot) -> None:
        """Let the window mirror CPU/memory into its status bar."""
        callback = getattr(self, "on_snapshot", None)
        if callback is not None:
            callback(snapshot)

    # -- rendering -------------------------------------------------------
    def _apply(self, snapshot: Snapshot) -> None:
        if self._spark is not None:
            self._spark.push(snapshot.cpu_percent)
        self._set_bar("cpu", snapshot.cpu_percent, format_percent(snapshot.cpu_percent))
        self._set_bar(
            "memory",
            snapshot.memory.percent,
            f"{format_percent(snapshot.memory.percent)} "
            f"({format_bytes(snapshot.memory.used)} / {format_bytes(snapshot.memory.total)})",
        )
        if snapshot.memory.swap_total:
            self._set_bar(
                "swap",
                snapshot.memory.swap_percent,
                f"{format_percent(snapshot.memory.swap_percent)} "
                f"({format_bytes(snapshot.memory.swap_used)} / "
                f"{format_bytes(snapshot.memory.swap_total)})",
            )
        else:
            self._set_bar("swap", 0.0, "未启用")

        load = snapshot.load
        self._set_text(
            "load",
            f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
            + (f" · {snapshot.core_count} 核" if snapshot.core_count else ""),
        )
        self._render_cores(snapshot.cpu_cores)
        self._render_disks(snapshot.disks)
        self._render_network(snapshot.network)

        self._set_text("host", snapshot.hostname or "—")
        self._set_text("kernel", snapshot.kernel or "—")
        self._set_text(
            "uptime",
            format_duration(snapshot.uptime) if snapshot.uptime else "—",
        )
        self._set_text("processes", str(snapshot.process_count) if snapshot.process_count else "—")
        own = self.sampler.process(os.getpid())
        if own.available:
            self._set_text(
                "vela",
                f"{format_percent(own.cpu_percent)} · {format_bytes(own.rss)}",
            )
        else:
            self._set_text("vela", "—")

    def _set_text(self, key: str, text: str) -> None:
        label = self._rows.get(key)
        if label is not None:
            label.set_text(text)
            label.set_tooltip_text(text)

    def _set_bar(self, key: str, fraction: float, text: str) -> None:
        bar = self._bars.get(key)
        if bar is not None:
            bar.set_fraction(max(0.0, min(1.0, fraction / 100.0)))
            # Colour by severity: the meter itself is drawn with cairo, so the
            # threshold colours are picked here rather than in CSS.
            if fraction >= 90:
                bar.set_colors("", self._meter_colors[2])
            elif fraction >= 75:
                bar.set_colors("", self._meter_colors[1])
            else:
                bar.set_colors("", self._meter_colors[0])
        self._set_text(key, text)

    def _render_cores(self, cores: Dict[str, float]) -> None:
        if self._core_box is None:
            return
        ordered = sorted(cores.items(), key=_core_sort_key)
        active = {label for label, _value in ordered}
        for label in self._core_cells:
            cell = self._core_cells[label]
            bar = self._core_bars[label]
            percent = self._core_labels[label]
            if label in active:
                value = dict(ordered)[label]
                bar.set_fraction(max(0.0, min(1.0, value / 100.0)))
                percent.set_text(f"{value:.0f}%")
                if not cell.get_visible():
                    cell.show()
            elif cell.get_visible():
                cell.hide()

    def _build_core_cells(self, labels: List[str]) -> None:
        container = self._core_box
        for child in list(container.get_children()):
            container.remove(child)
        self._core_bars.clear()
        self._core_labels.clear()
        self._core_cells.clear()
        self._core_rows.clear()
        columns = 2
        row: Optional[Gtk.Box] = None
        for index, label in enumerate(labels):
            if index % columns == 0:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                # Explicit height: GTK kept giving these rows 1px each because
                # the containing column is taller than the viewport and the
                # rows' natural height was not honoured.
                row.set_size_request(-1, CORE_ROW_HEIGHT)
                container.pack_start(row, False, False, 0)
                self._core_rows.append(row)
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            name = Gtk.Label(label=label.replace("cpu", "核"))
            name.set_xalign(0.0)
            name.get_style_context().add_class("vela-dim")
            percent = Gtk.Label(label="—")
            percent.set_xalign(1.0)
            percent.get_style_context().add_class("vela-dim")
            bar = Meter(cells=6)
            cell.pack_start(name, False, False, 0)
            cell.pack_start(bar, True, True, 0)
            cell.pack_end(percent, False, False, 0)
            if row is not None:
                row.pack_start(cell, True, True, 0)
            self._core_bars[label] = bar
            self._core_labels[label] = percent
            self._core_cells[label] = cell
        container.show_all()

    def _render_disks(self, disks: Sequence[DiskUsage]) -> None:
        for child in list(self._disk_box.get_children()):
            self._disk_box.remove(child)
        if not disks:
            self._disk_box.pack_start(_dim_label("无可用磁盘信息"), False, False, 0)
            self._disk_box.show_all()
            return
        for disk in disks:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Label(label=_short_mount(disk.mount))
            name.set_xalign(0.0)
            name.set_tooltip_text(disk.mount)
            name.get_style_context().add_class("vela-dim")
            value = Gtk.Label(
                label=f"{format_percent(disk.percent)} · 可用 {format_bytes(disk.free)}"
            )
            value.set_xalign(1.0)
            row.pack_start(name, False, False, 0)
            row.pack_end(value, False, False, 0)
            bar = Meter(cells=6)
            bar.set_fraction(max(0.0, min(1.0, disk.percent / 100.0)))
            if disk.percent >= 90:
                bar.set_colors("", self._meter_colors[2])
            elif disk.percent >= 75:
                bar.set_colors("", self._meter_colors[1])
            else:
                bar.set_colors("", self._meter_colors[0])
            self._disk_box.pack_start(row, False, False, 0)
            self._disk_box.pack_start(bar, False, False, 0)
        self._disk_box.show_all()

    def _render_network(self, rates: Sequence[NetworkRate]) -> None:
        for child in list(self._network_box.get_children()):
            self._network_box.remove(child)
        if not rates:
            self._network_box.pack_start(_dim_label("无网络接口"), False, False, 0)
            self._network_box.show_all()
            return
        limit = max(1, int(self.config.get("sysinfo.max_interfaces")))
        shown = rates
        note = ""
        if not shown and self.sampler.skip_loopback:
            # Restricted environments (containers, sandboxes) may only expose
            # loopback; showing it beats showing nothing.
            loopback = network_rates(
                {}, parse_net_dev(_safe_read(PROC_NET_DEV)), 1.0, False
            )
            shown = [rate for rate in loopback if rate.name == "lo"]
            if shown:
                note = "（仅回环接口）"
        if not shown:
            self._network_box.pack_start(_dim_label("无网络接口"), False, False, 0)
            self._network_box.show_all()
            return
        for rate in shown[:limit]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Label(label=rate.name)
            name.set_xalign(0.0)
            name.get_style_context().add_class("vela-dim")
            value = Gtk.Label(
                label=f"↓ {format_rate(rate.rx_rate)}  ↑ {format_rate(rate.tx_rate)}"
                + note
            )
            value.set_xalign(1.0)
            value.set_tooltip_text(
                f"累计 收 {format_bytes(rate.rx_total)} / 发 {format_bytes(rate.tx_total)}"
            )
            row.pack_start(name, False, False, 0)
            row.pack_end(value, False, False, 0)
            self._network_box.pack_start(row, False, False, 0)
        self._network_box.show_all()

    def _refresh_processes(self) -> None:
        if self._process_box is None:
            return
        for child in list(self._process_box.get_children()):
            self._process_box.remove(child)
        panes = list(self._pane_provider())[:6]
        if not panes:
            self._process_box.pack_start(_dim_label("无活动终端"), False, False, 0)
            self._process_box.show_all()
            return
        for title, pid in panes:
            usage = self.sampler.process(pid)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Label(label=title or f"pid {pid}")
            name.set_xalign(0.0)
            name.set_ellipsize(3)
            name.get_style_context().add_class("vela-dim")
            if usage.available:
                text = f"PID {usage.pid} · {format_percent(usage.cpu_percent)} · {format_bytes(usage.rss)}"
            else:
                text = "已退出"
            value = Gtk.Label(label=text)
            value.set_xalign(1.0)
            row.pack_start(name, False, False, 0)
            row.pack_end(value, False, False, 0)
            self._process_box.pack_start(row, False, False, 0)
        self._process_box.show_all()


def _dim_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.get_style_context().add_class("vela-dim")
    return label


def _short_mount(path: str, limit: int = 18) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1) :]


def _core_sort_key(item: Tuple[str, float]) -> Tuple[int, str]:
    """Order "cpu2" before "cpu10" while keeping unnumbered labels last."""
    label = item[0]
    digits = label[3:] if label.startswith("cpu") else ""
    if digits.isdigit():
        return (int(digits), label)
    return (1_000_000, label)
