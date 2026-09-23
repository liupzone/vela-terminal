# Vela Terminal 设计方案

日期：2026-09-22
目标平台：Ubuntu 20.04 LTS (Focal)、X11/Wayland、GTK 3.24 + VTE 2.91（系统自带）

## 1. 目标

做一个"当下主流终端该有的功能都有、且好看"的 Linux 终端模拟器，能在这台 Ubuntu 20.04
机器上直接运行、无需联网安装依赖。

对标功能集合（取自 GNOME Terminal / Tilix / Terminator / Kitty / WezTerm / iTerm2 的公共部分）：

1. 标签页（新建/关闭/重排/拖动/右键菜单/标题自动跟随）
2. 分屏（水平/垂直、无限嵌套、方向键在分屏间跳转、拖拽调整比例）
3. 命令面板（Ctrl+Shift+P，模糊搜索全部动作）
4. 全文搜索（Ctrl+Shift+F，正则、高亮、上一个/下一个、环绕）
5. 主题系统（内置多套配色，实时切换，跟随系统亮/暗可选）
6. 字体与缩放（Ctrl+= / Ctrl+- / Ctrl+0，行高列宽微调）
7. 超链接（Ctrl+点击打开、悬停提示）、路径/URL 识别
8. 真彩、Nerd 图标、emoji、CJK 宽字符、鼠标事件、括号粘贴、IME 中文输入
9. 剪贴板（复制/粘贴、选区自动复制可选、粘贴为转义串）
10. 回滚缓冲（可配置行数）、滚动条自动隐藏、视觉/声音响铃
11. 快捷键全量可配置（配置文件里改）
12. 持久化配置 + 运行时热重载
13. 桌面集成（.desktop、图标、可注册为默认终端）

## 2. 技术选型

**Python 3 + PyGObject + GTK 3 + VTE 2.91。**

VTE 是 GNOME Terminal、Tilix、Terminator 共用的终端内核，PTY、UTF-8、真彩、鼠标、
超链接、IME、括号粘贴全部开箱可用；我们只实现"界面层 + 交互层"，风险最低、见效最快。
机器上 `/usr/bin/python3`（3.8.10）已带 `gi`、`gir1.2-vte-2.91`，无需任何网络安装。

被否决的方案：

- **Rust/C++ 自研渲染内核（Alacritty/WezTerm 路线）**：性能上限更高，但要从零实现 VT
  解析、字形栅格化、GPU 渲染与输入法，本机也没有 Rust 工具链，工期与风险都不成比例。
- **Electron/Tauri 套壳 xterm.js**：好看但体积大、启动慢，且"终端"体验明显弱于 VTE。

## 3. 架构

```
bin/vela ──► vela/app.py (Gtk.Application + 命令行解析)
                 │
                 ├── vela/config.py    配置读写（TOML，纯 stdlib 解析）
                 ├── vela/theme.py     内置主题（含 16 色 ANSI 调色板 + UI 颜色）
                 ├── vela/keymap.py    快捷键解析/注册（Gtk.Application 动作加速键）
                 ├── vela/window.py    主窗口：HeaderBar / Notebook / 状态栏 / 覆盖层
                 │      ├── vela/tab.py        标签页（内含分屏树）
                 │      ├── vela/pane.py       分屏树（纯逻辑 + 容器适配）
                 │      ├── vela/terminal.py   终端视图（Vte.Terminal 封装）
                 │      ├── vela/searchbar.py  搜索条
                 │      └── vela/palette.py    命令面板
                 └── vela/style.py     GTK CSS（由主题颜色模板化生成）
```

数据流：主题/配置 → `style.py` 生成 CSS 与 VTE 调色板 → 应用到所有终端视图；
终端视图发信号（标题变化、目录变化、子进程退出）→ 标签页/状态栏/窗口标题同步更新。

## 4. 关键设计

- **分屏树**：`Leaf(终端)` 与 `Split(方向, 左右子节点)` 组成二叉树，UI 用 `Gtk.Paned`
  承载。分裂时替换父容器中的子控件，关闭时用兄弟节点顶替。方向键跳转按控件几何中心
  计算最近邻，符合直觉。
- **配置**：默认值内置，用户配置在 `~/.config/vela/config.toml`；Python 3.8 无
  `tomllib`，自带一个覆盖本项目配置子集的极简 TOML 解析器（分节、字符串、数字、布尔、
  数组、注释），写回由程序生成，保证可读。
- **主题**：每套主题 = 16 色 ANSI + 背景/前景/光标/选区 + 一组 UI 颜色；GTK CSS 由模板
  渲染，切换主题不需要重启。
- **错误处理**：配置文件损坏时回退默认值并提示；shell 启动失败在终端内打印原因并保留
  标签页；关闭有子进程存活的标签页时二次确认（可关）。

## 5. 测试与验收

- `tests/` 用标准库 `unittest`：配置往返、极简 TOML 解析、主题完整性（16 色、颜色合法）、
  快捷键解析、分屏树增删与几何跳转。
- 无头环境可跑的验收：`vela --version`、`vela --list-themes`、`vela --print-config`、
  单元测试全绿。
- 有显示环境可跑的验收：启动窗口、截图确认外观、真实 shell 交互（`echo`、`ls`、
  分屏、搜索、主题切换）。
- 安装验收：`install.sh` 后 `~/.local/bin/vela` 可执行、桌面菜单出现 Vela、可设为默认终端。

## 6. 明确不做（YAGNI）

- 自研 VT 解析/GPU 渲染、六边形协议图片显示、SSH 会话管理器、AI 助手面板、
  插件市场、Windows/macOS 支持。
