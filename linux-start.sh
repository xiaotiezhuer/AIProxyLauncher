#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname -s)" != Linux ]]; then
    printf '请在 Linux 系统上运行此启动脚本。\n' >&2
    exit 1
fi

python=""
for candidate in "${PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [[ -n "$candidate" ]] || continue
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -n "$resolved" ]] && "$resolved" -c 'import sys, tkinter; assert sys.version_info >= (3, 11)' 2>/dev/null; then
        python="$resolved"
        break
    fi
done

if [[ -z "$python" ]]; then
    cat >&2 <<'EOF'
未找到带 Tkinter 的 Python 3.11+ 运行环境。
请按发行版安装 Python 3.11+ 与 Tk 支持后重试：
  Ubuntu / Debian:  sudo apt update && sudo apt install -y python3 python3-tk
  Fedora / RHEL:    sudo dnf install -y python3 python3-tkinter
  Arch / Manjaro:   sudo pacman -S --needed python tk
  openSUSE:         sudo zypper install python3-tk
EOF
    exit 1
fi

exec "$python" -B launcher.py "$@"
