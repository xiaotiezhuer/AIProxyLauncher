#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ "$(uname -s)" != Linux ]]; then
    printf '请在 Linux 系统上构建 Linux 发行包。\n' >&2
    exit 1
fi

arch="${1:-$(uname -m)}"
python="${PYTHON:-python3}"
"$python" -c 'import sys, tkinter; assert sys.version_info >= (3, 11), sys.version'

stage="$(mktemp -d "${TMPDIR:-/tmp}/ai-proxy-launcher-linux.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT

"$python" -m venv "$stage/venv"
builder="$stage/venv/bin/python"
"$builder" -m pip install --upgrade pip
"$builder" -m pip install 'pyinstaller>=6.10'

run_with_display() {
    if [[ -n "${DISPLAY:-}" ]]; then
        "$@"
    elif command -v xvfb-run >/dev/null 2>&1; then
        xvfb-run -a -s "-screen 0 1280x900x24" "$@"
    else
        "$@"
    fi
}

echo "==> 1/4 Running unit tests..."
run_with_display "$builder" -B -m unittest discover -v -s .

app="AIProxyLauncher"
output="$PWD/构建输出/Linux-$arch"
mkdir -p "$output"

echo "==> 2/4 Building standalone Linux bundle with PyInstaller..."
args=(
    --noconfirm
    --onedir
    --windowed
    --name "$app"
    --distpath "$output"
    --workpath "$stage/work"
    --specpath "$stage"
)
if [[ -d "$PWD/icons" ]]; then
    args+=(--add-data "$PWD/icons:icons")
fi
"$builder" -m PyInstaller "${args[@]}" "$PWD/launcher.py"

bundle_dir="$output/$app"
test -x "$bundle_dir/$app"

# Include desktop shortcut installer & Linux guide inside the portable bundle
cp -f "$PWD/Linux使用说明.md" "$bundle_dir/" 2>/dev/null || true
if [[ -f "$PWD/icons/60/ide.png" ]]; then
    cp -f "$PWD/icons/60/ide.png" "$bundle_dir/aiproxylauncher.png"
fi
cat > "$bundle_dir/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_BIN="$APP_DIR/AIProxyLauncher"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/64x64/apps"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
if [[ -f "$APP_DIR/aiproxylauncher.png" ]]; then
    cp -f "$APP_DIR/aiproxylauncher.png" "$ICON_DIR/aiproxylauncher.png"
fi
cat > "$DESKTOP_DIR/aiproxylauncher.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=AI 代理启动器
GenericName=AI Proxy Launcher
Comment=Cross-platform AI Client & Web Proxy Launcher
Exec="$APP_BIN" %U
Icon=aiproxylauncher
Terminal=false
Categories=Network;Utility;Development;
StartupWMClass=AIProxyLauncher
DESKTOP
chmod +x "$APP_BIN" "$DESKTOP_DIR/aiproxylauncher.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
printf '已安装应用菜单快捷方式：%s\n' "$DESKTOP_DIR/aiproxylauncher.desktop"
EOF
chmod +x "$bundle_dir/install.sh"

if [[ "${RUN_GUI_SMOKE:-1}" == 1 ]]; then
    echo "==> 3/4 Running GUI smoke check on built Linux binary..."
    smoke="$stage/ui.txt"
    run_with_display "$bundle_dir/$app" --ui-smoke --report "$smoke"
    cat "$smoke"
    printf '\n'
fi

echo "==> 4/4 Packaging Linux binary & universal source archives..."
bin_archive="$output/$app-Linux-$arch.tar.gz"
tar -C "$output" -czf "$bin_archive" "$app"

src_root="$stage/$app-Linux"
mkdir -p "$src_root"
for file in \
    launcher.py win32net.py relay.py proxies.py envcheck.py tray.py \
    repair.py widgets.py dialogs.py interface.py macos.py linux.py \
    test_launcher.py test_macos.py test_linux.py \
    linux-start.sh build-linux.sh Linux使用说明.md 使用说明.md; do
    if [[ -f "$PWD/$file" ]]; then
        cp -f "$PWD/$file" "$src_root/"
    fi
done
chmod +x "$src_root/linux-start.sh" "$src_root/build-linux.sh" 2>/dev/null || true
if [[ -d "$PWD/icons" ]]; then
    cp -R "$PWD/icons" "$src_root/icons"
fi
src_archive="$output/$app-Linux-Source.tar.gz"
tar -C "$stage" -czf "$src_archive" "$app-Linux"

printf '\n%s\n%s\n%s\n' "$bundle_dir" "$bin_archive" "$src_archive"
