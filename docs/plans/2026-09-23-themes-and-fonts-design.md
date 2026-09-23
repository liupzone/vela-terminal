# Vela：更多配色主题与字体配置

日期：2026-09-23

## 1. 目标

**主题**：从 10 套扩到 20 套以上，覆盖主流配色方案，每套都包含完整的 16 色 ANSI
调色板与界面配色。

**字体**：

- 首选项里用**系统实际检测到的等宽字体**做下拉选择，而不是让人手打族名
- 实时预览（用所选字体渲染一段示例文本）
- 常用调节项：字号、行高、字宽、粗体、亮色、中英文分别指定
- 一键下载并安装流行编程字体（JetBrains Mono、Fira Code、Hack 等）到
  `~/.local/share/fonts`，装完自动出现在下拉里

## 2. 为什么这样做

**主题是纯数据，直接加。** 每套主题就是 16 色 ANSI + 一组 UI 颜色，`theme.py` 已经有
完整定义和校验（`tests/test_theme.py` 断言 16 色齐全、颜色合法、CSS 可生成）。加主题
零风险，只要颜色值正确。

**字体选择器必须枚举系统字体。** 本机实测可用的等宽字体只有 16 个（Ubuntu Mono、
DejaVu Sans Mono、Liberation Mono、Noto Mono/CJK、WenQuanYi 等），**没有** JetBrains
Mono / Fira Code / Hack，也没有任何 Nerd Font。让用户手打族名很容易打错且没有反馈，
所以用 `PangoCairo.FontMap.list_families()` 枚举真实存在的字体，并从中挑出等宽的。

**字体下载做成显式动作而不是自动执行。** 需要联网，可能失败（实测首次下载偶发超时，
重试 5.5 秒完成）。因此：只在用户点「下载」时联网，带进度与错误提示，失败可重试，
绝不阻塞启动或界面。

## 3. 架构

```
vela/theme.py          新增 10+ 套主题（纯数据）
vela/fonts.py          字体枚举（Pango）+ 下载安装（curl/urllib + fontconfig 刷新）
vela/prefs.py          字体选择器、预览、下载按钮；主题下拉分组
vela/style.py          字体预览与下载区的样式
vela/config.py         [fonts] 配置节
```

## 4. 关键设计

- **等宽判定**：`Pango.FontFamily.is_monospace()`，比按名字猜（含 "mono"）准确。
- **中文字体分离**：终端常见需求是英文用编程字体、中文用 CJK 字体。VTE 只接受一个
  `Pango.FontDescription`，但 Pango 支持 fallback 列表——用
  `FontDescription` + `Pango.AttrFontDesc` 组合不方便，改为提供「中文回退字体」配置项，
  在描述串里按 `族名1, 族名2` 形式交给 fontconfig 解析。
- **下载源与校验**：从 GitHub Releases 取官方压缩包；解压后**校验 TTF/OTF 魔数**
  （`00010000` / `OTTO` / `true`）再安装，避免把 HTML 错误页当字体装进去。
- **幂等安装**：按字体名建子目录，重复安装覆盖而非累积；安装后调用
  `fc-cache -f` 刷新，字体立即可用。
- **可注入的下载器**：`fonts.py` 的下载函数接受 opener，测试用假响应验证流程，
  不真的联网。

## 5. 测试与验收

- `tests/test_theme.py` 扩展：新主题全部通过既有完整性校验；主题名与显示名唯一；
  每套主题在亮/暗背景下前景与背景的对比度达到可读阈值。
- `tests/test_fonts.py`：等宽过滤、族名排序与去重、压缩包内字体文件筛选、魔数校验
  （拒绝 HTML/空文件）、安装目录幂等、下载失败的错误信息、配置往返。
- `tools/ui_smoke.py`：主题下拉能列出全部主题并切换；字体下拉列出系统等宽字体且
  切换后终端字体真的变化；预览区跟随选择更新。

## 6. 明确不做（YAGNI）

- 主题编辑器 / 从网络导入主题（iTerm2 `.itermcolors`、Windows Terminal JSON）
- 字体子集化、可变字体轴调节（weight/width 滑杆）
- 自动后台更新字体、字体许可协议界面
