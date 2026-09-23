#!/usr/bin/env bash
# Install Vela Terminal for the current user.
#
# Usage:  ./install.sh [--prefix DIR] [--uninstall] [--no-desktop]
#
# The terminal itself needs nothing beyond Python 3 and the GTK/VTE packages
# that ship with Ubuntu 20.04.  This script only links the launcher, installs
# the desktop entry and optionally registers Vela as the default terminal.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${HOME}/.local"
DESKTOP=1
UNINSTALL=0

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            PREFIX="${1#*=}"
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        --no-desktop)
            DESKTOP=0
            shift
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            echo "未知参数：$1" >&2
            usage 1
            ;;
    esac
done

APP_DIR="${PREFIX}/share/vela-terminal"
BIN_DIR="${PREFIX}/bin"
APPLICATIONS_DIR="${PREFIX}/share/applications"
ICONS_DIR="${PREFIX}/share/icons/hicolor/scalable/apps"
LAUNCHER="${BIN_DIR}/vela"
DESKTOP_FILE="${APPLICATIONS_DIR}/io.github.vela.Vela.desktop"
ICON_FILE="${ICONS_DIR}/io.github.vela.Vela.svg"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m警告：\033[0m %s\n' "$*" >&2; }

check_dependencies() {
    local python="/usr/bin/python3"
    if [[ ! -x "${python}" ]]; then
        echo "找不到 ${python}，请先安装 python3。" >&2
        exit 1
    fi
    if ! "${python}" - <<'PY'
import sys
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Vte", "2.91")
    from gi.repository import Gtk, Vte  # noqa: F401
except Exception as error:  # noqa: BLE001
    sys.stderr.write(f"缺少依赖：{error}\n")
    raise SystemExit(1)
raise SystemExit(0)
PY
    then
        cat >&2 <<'EOF'
缺少 GTK 3 / VTE 运行库。请执行：
  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
EOF
        exit 1
    fi
}

do_uninstall() {
    info "卸载 Vela Terminal"
    rm -f "${LAUNCHER}" "${DESKTOP_FILE}" "${ICON_FILE}"
    rm -rf "${APP_DIR}"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
    info "已卸载。用户配置保留在 ${XDG_CONFIG_HOME:-$HOME/.config}/vela/config.toml"
    exit 0
}

if [[ "${UNINSTALL}" -eq 1 ]]; then
    do_uninstall
fi

check_dependencies

info "安装到 ${APP_DIR}"
mkdir -p "${APP_DIR}" "${BIN_DIR}" "${APPLICATIONS_DIR}" "${ICONS_DIR}"

for item in vela bin share README.md LICENSE; do
    if [[ -e "${SOURCE_DIR}/${item}" ]]; then
        rm -rf "${APP_DIR:?}/${item}"
        cp -r "${SOURCE_DIR}/${item}" "${APP_DIR}/"
    fi
done

cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 "${APP_DIR}/bin/vela" "\$@"
EOF
chmod 755 "${LAUNCHER}"

if [[ "${DESKTOP}" -eq 1 && -f "${SOURCE_DIR}/share/io.github.vela.Vela.svg" ]]; then
    cp "${SOURCE_DIR}/share/io.github.vela.Vela.svg" "${ICON_FILE}"
    sed "s|@EXEC@|${LAUNCHER}|g" \
        "${SOURCE_DIR}/share/io.github.vela.Vela.desktop.in" > "${DESKTOP_FILE}"
    chmod 644 "${DESKTOP_FILE}" "${ICON_FILE}"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "${PREFIX}/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
    info "已安装桌面菜单项"
fi

if ! command -v vela >/dev/null 2>&1; then
    case ":${PATH}:" in
        *":${BIN_DIR}:"*) ;;
        *)
            warn "${BIN_DIR} 不在 PATH 中，请在 ~/.bashrc 中加入："
            warn "  export PATH=\"${BIN_DIR}:\$PATH\""
            ;;
    esac
fi

cat <<EOF

安装完成。

  启动：            vela
  查看主题：        vela --list-themes
  指定工作目录：    vela --working-directory ~/projects
  配置文件：        ${XDG_CONFIG_HOME:-$HOME/.config}/vela/config.toml
  卸载：            ${SOURCE_DIR}/install.sh --uninstall

设为系统默认终端（可选）：
  sudo update-alternatives --install /usr/bin/x-terminal-emulator \\
      x-terminal-emulator ${LAUNCHER} 50
EOF
