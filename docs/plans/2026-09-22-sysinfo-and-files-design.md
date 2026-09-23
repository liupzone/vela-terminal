# Vela：系统性能面板与文件打开能力

日期：2026-09-22
依赖：Vela Terminal 1.0.0（GTK 3.24 + VTE 2.91 + GtkSourceView 4 + GdkPixbuf，均为系统自带）

## 1. 目标

按需求补两块能力，对标 Wave 的对应部分：

**a. 系统性能（sysinfo）**

一个可开关的侧边面板，实时显示本机资源占用，并且不引入任何第三方依赖
（不装 psutil，直接读 `/proc` 与 `os.statvfs`）。

- CPU：总体使用率 + 每个核心使用率、负载均值、核心数
- 内存：已用/总量 + 交换分区
- 磁盘：根分区与家目录所在分区使用率
- 网络：实时收发速率 + 累计流量
- 进程：进程总数、当前终端前台进程、Vela 自身占用
- 系统：内核版本、运行时长、主机名
- 每个终端分屏旁显示其子进程的 CPU/内存占用

**b. 直接打开 files**

终端里出现的路径、文件名、URL 可以直接打开，并且按类型选最合适的查看方式：

- 文本/代码：用 GtkSourceView 打开（语法高亮、行号、可编辑、可保存、查找）
- 图片：用 GdkPixbuf 打开（缩放、适应窗口）
- 目录：调系统文件管理器（`xdg-open`，失败时回退到 `nautilus`）
- 其他：交给 `xdg-open`
- 编辑当前命令里正在输入的那一段路径

## 2. 为什么这样做

**sysinfo 用 `/proc` 而不是 psutil。** Vela 的定位是「Ubuntu 20.04 上开箱即用、不联网装依赖」，
`/proc/stat`、`/proc/meminfo`、`/proc/net/dev`、`/proc/diskstats`、`os.statvfs` 已经能覆盖
上述全部指标，加一个第三方库只会破坏这个前提。代价是要自己处理计数器差分与首次采样，
这部分逻辑放在纯函数里，可以直接单元测试。

**文件打开用「按类型分发」而不是内嵌一个编辑器。** 终端里最常打开的其实是文本和日志；
用 GtkSourceView 打开比调外部编辑器更快、也更贴合终端工作流。图片同理。
其余类型交给 `xdg-open` 是 Linux 桌面的既定约定，用户自己的默认程序才是对的。

**为什么不做内联命令块。** 前面已实测：本机 VTE 0.60.3 不支持 OSC 133 语义提示符
（无 `get_prompt_state`，也没有 prompt 相关信号，该支持需 VTE 0.68+），因此无法拿到
「每个命令块对应的输出文本范围」。本轮不引入 PTY 代理，该能力留作后续独立模块。

## 3. 架构

```
vela/sysinfo.py        纯逻辑采样（/proc 解析 + 差分）+ SysinfoPanel（侧边面板 UI）
vela/files.py          打开分发（xdg-open / nautilus 回退）+ 文件类型判定
vela/viewer.py         内置查看器：GtkSourceView 文本视图、GdkPixbuf 图片视图
vela/window.py         集成：动作、快捷键、菜单、命令面板、状态栏、侧边栏容器
vela/terminal.py       暴露分屏子进程信息；命中路径时打开；Ctrl+点击路径
vela/config.py         [sysinfo] 与 [files] 两节配置
vela/style.py          面板与查看器的样式
```

数据流：`sysinfo.Sampler` 每 N 秒读一次 `/proc` 并算出速率/占用，`SysinfoPanel` 负责画；
文件打开走 `files.open_path()`，它按扩展名与 `stat` 结果决定交给 `viewer` 还是 `xdg-open`。

## 4. 关键设计

- **采样器可注入**：`Sampler` 接受「读文件」的可调用对象，测试里喂假的 `/proc` 内容，
  就能在没有真实负载的情况下断言 CPU 差分、速率计算与边界情况（计数器回绕、首次采样）。
- **CPU 使用率**：两次采样的 `idle`/`total` 差分，首次采样返回 0 而不是瞎猜。
- **网络速率**：`/proc/net/dev` 累计字节差分除以时间间隔；排除 `lo` 可配置。
- **进程占用**：按 `child_pid` 读 `/proc/<pid>/stat` 与 `/proc/<pid>/statm` 算 CPU 时间与 RSS。
- **路径识别**：优先用 VTE 的 `match_add_regex` 标记路径，`Ctrl+点击` 打开；
  同时提供动作，直接打开「光标处/选区里」的路径。
- **文件写入安全**：查看器保存前校验原文件未被外部修改，避免覆盖别人的改动。
- **错误处理**：打不开就写到状态栏并给出原因；`/proc` 读失败只让对应指标显示 `—`，
  不影响其它指标和终端本身。

## 5. 测试与验收

- `tests/test_sysinfo.py`：注入假 `/proc` 数据，验证 CPU 差分、网络速率、内存换算、
  计数器回绕、首次采样、`/proc` 缺字段时的降级。
- `tests/test_files.py`：扩展名与类型分发（文本/图片/目录/未知）、不存在路径、
  无权限路径、`xdg-open` 缺失时的回退顺序、可执行文件的判定。
- `tools/ui_smoke.py` 扩展：打开 sysinfo 面板并断言指标已填充、用查看器打开一个真实
  文本文件与图片文件、断言窗口标题与内容、关闭查看器。
- 真机验收：面板数值与 `top`/`free`/`ip -s link` 同量级；打开 README、PNG、
  目录三种目标均成功。

## 6. 明确不做（YAGNI）

- PTY 代理与内联命令块、GPU 占用、传感器温度、远程主机监控
- 内嵌完整 IDE（多标签编辑器、LSP、git 集成）
- 文件管理器界面；目录一律交给系统文件管理器
