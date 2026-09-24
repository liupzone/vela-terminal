"""Application entry point and command line interface."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from typing import List, Optional, Sequence

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, keymap  # noqa: E402
from . import config as config_mod  # noqa: E402
from . import theme as theme_mod  # noqa: E402
from .window import MainWindow  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vela",
        description="Vela Terminal - 现代、美观的 GTK 终端模拟器",
        epilog="示例：vela --working-directory ~/projects --maximize",
    )
    parser.add_argument(
        "--version", action="store_true", help="打印版本号后退出"
    )
    parser.add_argument("--list-themes", action="store_true", help="列出内置主题")
    parser.add_argument(
        "--print-config", action="store_true", help="打印当前配置（TOML）"
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="校验配置文件并报告问题，不启动界面",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="使用指定的配置文件（默认 ~/.config/vela/config.toml）",
    )
    parser.add_argument(
        "--working-directory",
        "-d",
        metavar="DIR",
        help="新终端的初始工作目录",
    )
    parser.add_argument(
        "--title", metavar="TEXT", help="首个标签页的标题"
    )
    parser.add_argument(
        "--maximize", action="store_true", help="以最大化状态启动"
    )
    parser.add_argument(
        "--fullscreen", action="store_true", help="以全屏状态启动"
    )
    parser.add_argument(
        "--theme", metavar="NAME", help="覆盖启动主题（不写入配置文件）"
    )
    parser.add_argument(
        "--no-headerbar",
        action="store_true",
        help="隐藏标题栏（适合平铺窗口管理器）",
    )
    parser.add_argument(
        "--execute",
        "-e",
        nargs=argparse.REMAINDER,
        metavar="CMD",
        help="在终端中执行命令，而不是启动 shell",
    )
    parser.add_argument(
        "--capture",
        metavar="FILE",
        help="启动后把窗口截图保存到 FILE 并退出（用于外观验证）",
    )
    parser.add_argument(
        "--capture-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="配合 --capture 使用的等待秒数（默认 2）",
    )
    parser.add_argument(
        "--app-id",
        metavar="ID",
        help="覆盖 D-Bus 应用 ID（默认 io.github.vela.Vela），用于与已运行的实例并存",
    )
    return parser


def print_config(config: config_mod.Config) -> None:
    sys.stdout.write(config.to_toml())
    for problem in config.problems:
        sys.stderr.write(f"warning: {problem}\n")


class VelaApplication(Gtk.Application):
    """Owns windows and the application-wide actions."""

    def __init__(
        self,
        config: config_mod.Config,
        options: argparse.Namespace,
        application_id: str = APP_ID,
    ) -> None:
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.config = config
        self.options = options
        self.windows: List[MainWindow] = []

    # -- lifecycle -------------------------------------------------------
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self._install_app_actions()
        self._install_shortcuts()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        self._launch({})
        return 0

    def do_activate(self) -> None:
        if not self.windows:
            self._launch({})
        else:
            self.windows[-1].present()

    def _launch(self, overrides: dict) -> MainWindow:
        window = MainWindow(self, self.config)
        self.windows.append(window)
        window.connect("destroy", self._on_window_destroyed)
        cwd = overrides.get("cwd") or self._initial_cwd()
        argv = overrides.get("argv") or self._initial_argv()
        tab = window.new_tab(cwd=cwd, argv=argv)
        # Restoring the previous session replaces that starter tab, so it only
        # happens when the user has not asked for something specific on the
        # command line.
        if not (cwd or argv) and not overrides.get("title"):
            window.restore_session_if_enabled()
        # Realize the window only now that it has content; see the note in
        # MainWindow.__init__.
        window.show_all()
        # Overlays are part of the window, so they are shown by show_all() and
        # then hidden again until the user asks for them.
        window.palette.hide()
        window.search_revealer.set_reveal_child(False)
        title = overrides.get("title") or self.options.title
        if title:
            tab.title_override = str(title)
            tab.refresh_title()
        if self.options.maximize:
            window.maximize()
        if self.options.fullscreen:
            window.fullscreen()
        window.present()
        for problem in self.config.problems:
            window.show_status(problem, 10, "warning")
        if self.options.capture:
            self._schedule_capture(window)
        return window

    def _schedule_capture(self, window: MainWindow) -> None:
        target = os.path.abspath(os.path.expanduser(self.options.capture))
        delay = max(0.2, float(self.options.capture_delay))
        import gi as _gi

        _gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk

        def grab() -> bool:
            # Compositors do not repaint occluded windows, which would capture a
            # stale frame; keep the window on top while grabbing.
            window.set_keep_above(True)
            window.present()
            gdk_window = window.get_window()
            if gdk_window is None:
                return True
            width = gdk_window.get_width()
            height = gdk_window.get_height()
            pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, width, height)
            if pixbuf is None:
                sys.stderr.write("错误：截图失败（窗口未渲染）\n")
            else:
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                pixbuf.savev(target, "png", [], [])
                print(f"已保存截图：{target} ({width}x{height})")
            self.quit_app()
            return False

        GLib.timeout_add(int(delay * 1000), grab)

    def _initial_cwd(self) -> Optional[str]:
        directory = self.options.working_directory
        if directory:
            expanded = os.path.expanduser(directory)
            if os.path.isdir(expanded):
                return expanded
        return os.getcwd() if os.path.isdir(os.getcwd()) else None

    def _initial_argv(self) -> Optional[List[str]]:
        command = self.options.execute
        if command:
            return list(command)
        return None

    def _on_window_destroyed(self, window: MainWindow) -> None:
        if window in self.windows:
            self.windows.remove(window)
        if not self.windows:
            self.quit()

    def window_closed(self, window: MainWindow) -> None:
        if window in self.windows:
            self.windows.remove(window)
        if not self.windows:
            self.quit()

    # -- actions ---------------------------------------------------------
    def _install_app_actions(self) -> None:
        for name, callback in (
            ("new-window", lambda *_: self.new_window()),
            ("reload-config", lambda *_: self.reload_config()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    def _install_shortcuts(self) -> None:
        bindings = self.config.keybindings()
        keymap.apply(self, bindings, "win")
        app_only = {"quit"}
        keymap.apply(self, {k: v for k, v in bindings.items() if k in app_only}, "app")

    def new_window(self) -> None:
        self._launch({})

    def reload_config(self) -> None:
        self.config.reload()
        for window in self.windows:
            window.reload_config()

    def quit_app(self) -> None:
        for window in list(self.windows):
            window.destroy()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    options = parser.parse_args(list(argv) if argv is not None else None)

    if options.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if options.list_themes:
        print("可用主题（配置项 appearance.theme）：")
        for row in theme_mod.describe():
            print("  " + row)
        return 0

    config_path = options.config
    if config_path:
        os.environ[config_mod.CONFIG_DIR_ENV] = os.path.dirname(
            os.path.abspath(config_path)
        )
    config = config_mod.Config.load(config_path)
    if options.print_config:
        print_config(config)
        return 0
    if options.check_config:
        if config.problems:
            for problem in config.problems:
                print(f"问题：{problem}", file=sys.stderr)
            return 1
        print(f"配置文件正常：{config.path}")
        return 0

    if options.theme:
        if options.theme not in theme_mod.all_names():
            parser.error(
                f"未知主题 {options.theme!r}；可用主题见 vela --list-themes"
            )
        config.set("appearance.theme", options.theme)
    if options.no_headerbar:
        config.set("window.show_headerbar", False)

    if not Gtk.init_check(None)[0]:
        sys.stderr.write(
            "错误：无法连接到显示服务器。请确认 DISPLAY/WAYLAND_DISPLAY 环境变量，"
            "或使用 vela --print-config 等无界面命令。\n"
        )
        return 2

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # An explicit --app-id lets tests and screenshot runs coexist with an
    # already-running Vela instead of being absorbed by it as a remote client.
    application_id = options.app_id or APP_ID
    app = VelaApplication(config, options, application_id)
    return int(app.run([sys.argv[0]]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
