#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
    printf '请在 macOS 上构建 .app。\n' >&2
    exit 1
fi

arch="${1:-$(uname -m)}"
case "$arch" in
    arm64|x86_64|universal2) ;;
    *) printf '目标架构须为 arm64、x86_64 或 universal2。\n' >&2; exit 1 ;;
esac

python="${PYTHON:-python3}"
"$python" -c 'import sys, tkinter; assert sys.version_info >= (3, 11)'
stage="$(mktemp -d "${TMPDIR:-/tmp}/ai-proxy-launcher.XXXXXXXX")"
trap '/bin/rm -rf -- "$stage"' EXIT
"$python" -m venv "$stage/venv"
builder="$stage/venv/bin/python"
"$builder" -m pip install 'pyinstaller>=6.10'
"$builder" -B -m unittest discover -v -s .

app="AIProxyLauncher"
output="$PWD/构建输出/macOS-$arch"
mkdir -p "$output"

signing=()
if [[ -n "${MAC_CODESIGN_IDENTITY:-}" ]]; then
    signing=(--codesign-identity "$MAC_CODESIGN_IDENTITY")
fi
args=(--noconfirm --windowed --onedir --name "$app" --osx-bundle-identifier local.aiproxylauncher
      --target-arch "$arch" --distpath "$output" --workpath "$stage/work" --specpath "$stage")
if [[ -d "$PWD/icons" ]]; then
    args+=(--add-data "$PWD/icons:icons")
fi
if [[ ${#signing[@]} -gt 0 ]]; then
    args+=("${signing[@]}")
fi
"$builder" -m PyInstaller "${args[@]}" "$PWD/launcher.py"
bundle="$output/$app.app"
/usr/bin/lipo -archs "$bundle/Contents/MacOS/$app"
/usr/bin/codesign --verify --deep --strict "$bundle"

if [[ "${RUN_GUI_SMOKE:-1}" == 1 ]]; then
    smoke="$stage/ui.txt"
    /usr/bin/open -W -n -a "$bundle" --args --ui-smoke --report "$smoke"
    /bin/cat "$smoke"
fi

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$bundle" "$output/$app-macOS-$arch.zip"
printf '\n%s\n' "$bundle" "$output/$app-macOS-$arch.zip"
