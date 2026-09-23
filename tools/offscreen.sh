#!/usr/bin/env bash
# Run a command against a throwaway virtual display.
#
# Why this exists: the UI smoke test opens real windows.  Run against the user's
# session that means stealing focus and popping windows on top of their work.
# Xvfb is not installed here and sudo is unavailable, so the package is
# unpacked into a local directory on first use and run from there.  Nothing is
# installed system-wide and the user's display is never touched.
#
# Usage:  tools/offscreen.sh <command> [args...]
#         tools/offscreen.sh -- python3 tools/ui_smoke.py --out /tmp/out

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "${HERE}")"
LOCAL_XVFB="${VELA_XVFB_DIR:-${ROOT}/.tools/xvfb}"
XVFB_BIN="${LOCAL_XVFB}/root/usr/bin/Xvfb"
DISPLAY_NUM="${VELA_XVFB_DISPLAY:-99}"
SCREEN="${VELA_XVFB_SCREEN:-1600x1000x24}"

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
    usage 0
fi
# Allow an explicit "--" separator so callers can pass their own flags.
if [[ "$1" == "--" ]]; then
    shift
fi

install_xvfb() {
    echo "==> 首次使用：把 Xvfb 解包到 ${LOCAL_XVFB}（不安装到系统）"
    mkdir -p "${LOCAL_XVFB}"
    # apt-get writes its progress to stdout, so the downloaded filename is taken
    # from the filesystem rather than parsed out of that output.
    (cd "${LOCAL_XVFB}" && apt-get download xvfb >/dev/null 2>&1) || true
    local deb
    deb="$(find "${LOCAL_XVFB}" -maxdepth 1 -name 'xvfb_*.deb' -print -quit)"
    if [[ -z "${deb}" ]]; then
        echo "错误：无法下载 xvfb 包，请检查网络。" >&2
        exit 1
    fi
    rm -rf "${LOCAL_XVFB}/root"
    dpkg-deb -x "${deb}" "${LOCAL_XVFB}/root"
    rm -f "${deb}"
    if [[ ! -x "${XVFB_BIN}" ]]; then
        echo "错误：解包后仍找不到 Xvfb。" >&2
        exit 1
    fi
}

if [[ ! -x "${XVFB_BIN}" ]]; then
    install_xvfb
fi

# Pick a display number that is not already in use.
while [[ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]]; do
    DISPLAY_NUM=$((DISPLAY_NUM + 1))
done

cleanup() {
    if [[ -n "${XVFB_PID:-}" ]] && kill -0 "${XVFB_PID}" 2>/dev/null; then
        kill "${XVFB_PID}" 2>/dev/null || true
        wait "${XVFB_PID}" 2>/dev/null || true
    fi
    rm -f "/tmp/.vela-xvfb-${DISPLAY_NUM}.log"
}
trap cleanup EXIT INT TERM

"${XVFB_BIN}" ":${DISPLAY_NUM}" -screen 0 "${SCREEN}" -nolisten tcp \
    > "/tmp/.vela-xvfb-${DISPLAY_NUM}.log" 2>&1 &
XVFB_PID=$!

# Wait for the server to accept connections.
for _ in $(seq 1 50); do
    if [[ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]]; then
        break
    fi
    if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
        echo "错误：Xvfb 启动失败：" >&2
        cat "/tmp/.vela-xvfb-${DISPLAY_NUM}.log" >&2 || true
        exit 1
    fi
    sleep 0.1
done

export DISPLAY=":${DISPLAY_NUM}"
export GDK_BACKEND=x11
export VELA_OFFSCREEN=1
echo "==> 离屏显示 ${DISPLAY}（不会出现在你的桌面上）"
"$@"
