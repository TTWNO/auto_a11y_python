#!/bin/bash
# Build Auto A11y desktop app for Windows (x86_64)
# Creates an NSIS installer in electron/dist/
#
# Cross-builds from macOS. Nothing here executes a Windows binary:
#
#   * pip installs win_amd64 wheels with --target, so no python.exe runs;
#   * the browsers are fetched from Playwright\'s CDN by URL rather than
#     through `playwright install`, which would need the Windows Python;
#   * electron-builder downloads its own wine to build the NSIS installer.
#
# Everything is x86_64. Windows 11 on Apple Silicon runs it under
# emulation, which is slower but correct; on an Intel box it is native.
# Mixing an arm64 Electron shell with an x64 Python bundle is the one
# combination to avoid, so the arch is pinned explicitly at every step.
#
# Test the result on Windows before shipping it. Nothing here can tell
# you it launches.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# Deliberately NOT build/staging. That directory belongs to the macOS
# build, and both ./python and ./.venv are symlinks into its python/.
# Step 1 starts with `rm -rf`, so sharing it replaces the development
# interpreter with a Windows one and every type checker, test run and
# hook stops working until the macOS build is run again.
BUILD_DIR="$PROJECT_DIR/build/staging-windows"
ELECTRON_DIR="$PROJECT_DIR/electron"

# Versions
PYTHON_VERSION="3.12.8"
PYTHON_BUILD_TAG="20250106"
MONGO_VERSION="7.0.17"
# ffmpeg: BtbN FFmpeg-Builds win64 gpl static. Pin to a DATED release tag
# (not "latest") and capture its real SHA-256 on the build host via
#   curl -L <url> | shasum -a 256
# The build FAILS on mismatch. The zip ships bin/ffmpeg.exe + bin/ffprobe.exe.
FFMPEG_WIN_TAG="autobuild-2026-08-13-17-03"
FFMPEG_WIN_ASSET="ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"
FFMPEG_WIN_SHA256="dcaee93310ba85b9e52b343f518e39b8e72c2799a5bcb6d35b1c0127e885fe65"

# GTK3 runtime for WeasyPrint. Windows has no system cairo/pango, and
# WeasyPrint loads both through ctypes off PATH — so the runtime ships
# with the app and electron/process-manager.js prepends its bin to PATH.
# gvsbuild is the build the WeasyPrint docs point Windows users at.
GTK_VERSION="2026.8.0"
GTK_SHA256="9be43d9029749062d2637070c856e73f8a1327a72259d08326b38155c139ecdc"

# macOS ships shasum, Linux ships sha256sum. The build now runs on
# either, so it asks for whichever is present rather than assuming.
_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

echo "=== Auto A11y Windows Build ==="
echo "Project: $PROJECT_DIR"
echo "Build staging: $BUILD_DIR"

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{python,app,mongodb/bin,chromium,ffmpeg/bin,gtk}

# -------------------------------------------------------
# 1. Download portable Python (Windows MSVC standalone)
# -------------------------------------------------------
echo ""
echo "--- Step 1: Portable Python $PYTHON_VERSION (windows-msvc) ---"
PYTHON_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
PYTHON_ARCHIVE="$BUILD_DIR/python-standalone.tar.gz"
if [ ! -f "$PYTHON_ARCHIVE" ]; then
    echo "Downloading portable Python..."
    curl -L -o "$PYTHON_ARCHIVE" "$PYTHON_URL"
fi
echo "Extracting Python..."
tar -xzf "$PYTHON_ARCHIVE" -C "$BUILD_DIR/python" --strip-components=1
# On the windows-msvc standalone build the interpreter is python/python.exe
# with no bin/ directory at all. electron/process-manager.js resolves that
# layout when process.platform is win32; keep the two in step.
PORTABLE_PYTHON="$BUILD_DIR/python/python.exe"
# Never executed — this build does not run Windows binaries. Checked
# because process-manager.js resolves exactly this path at launch.
[ -f "$PORTABLE_PYTHON" ] \
    || { echo "ERROR: no python.exe after extraction: $PORTABLE_PYTHON" >&2; exit 1; }
echo "Python extracted: $PORTABLE_PYTHON"

# -------------------------------------------------------
# 2. Install pip dependencies
# -------------------------------------------------------
echo ""
echo "--- Step 2: Install pip dependencies ---"
# The Windows interpreter cannot run here, so pip resolves and unpacks
# Windows wheels into its site-packages instead of installing them. 135
# of the 136 pinned requirements publish win_amd64 wheels; the one that
# does not is pure Python and is handled below.
# The interpreter that does the resolving, never one that gets bundled.
# Default to the repo's own, because step 5 reads the Playwright browser
# revisions out of it and those must be the versions this app pins.
HOST_PYTHON="${HOST_PYTHON:-$PROJECT_DIR/python/bin/python3.12}"
command -v "$HOST_PYTHON" >/dev/null 2>&1 || HOST_PYTHON="python3"
command -v "$HOST_PYTHON" >/dev/null 2>&1 \
    || { echo "ERROR: no host python; set HOST_PYTHON" >&2; exit 1; }
# Without playwright the revision lookup below silently falls back to
# whatever defaults are compiled in, and the bundle ends up with browsers
# the app will not use.
"$HOST_PYTHON" -c "import playwright" >/dev/null 2>&1 \
    || { echo "ERROR: $HOST_PYTHON has no playwright installed; the browser revisions come from it" >&2; exit 1; }

SITE_PACKAGES="$BUILD_DIR/python/Lib/site-packages"
mkdir -p "$SITE_PACKAGES"

# A handful of packages in the closure publish an sdist and no wheel at
# all, which --only-binary=:all: treats as a hard failure — including
# docopt, which nothing here requires directly (webvtt-py pulls it in).
# Each is pure Python with no build step, so a wheel built here is byte
# for byte what a Windows pip would produce.
#
# The list is written out rather than discovered because building an
# arbitrary sdist for a platform you are not on is a decision worth
# making deliberately. To find a new one: pip names it, one per run, as
# "No matching distribution found for X".
_wheelhouse="$BUILD_DIR/_wheelhouse"
mkdir -p "$_wheelhouse"

# Read from requirements.txt so it cannot drift from the version the
# macOS build and CI use.
WCAG_PIN="$(grep -iE '^wcag-contrast-ratio==' "$PROJECT_DIR/requirements.txt")"
[ -n "$WCAG_PIN" ] || { echo "ERROR: wcag-contrast-ratio pin not found in requirements.txt" >&2; exit 1; }
# docopt is transitive and appears in no requirements file, so its
# version is pinned here.
for _pure_sdist in "$WCAG_PIN" "docopt==0.6.2"; do
    "$HOST_PYTHON" -m pip wheel --no-deps --wheel-dir "$_wheelhouse" "$_pure_sdist"
done

# --platform/--only-binary is what makes this a cross-install: pip picks
# wheels for the target rather than for this machine. It refuses to guess
# about anything that would need building, which is the behaviour we
# want — a silent host-native wheel here would fail on the user's PC.
# --find-links lets it satisfy the sdist-only names from the wheelhouse
# above without relaxing that rule for anything else.
"$HOST_PYTHON" -m pip install \
    --target "$SITE_PACKAGES" \
    --platform win_amd64 \
    --python-version "${PYTHON_VERSION%.*}" \
    --implementation cp \
    --only-binary=:all: \
    --find-links "$_wheelhouse" \
    --upgrade \
    -r "$PROJECT_DIR/requirements-windows.txt"

# wcag-contrast-ratio is deliberately absent from requirements-windows.txt
# (see the header there), so it is requested by name.
"$HOST_PYTHON" -m pip install \
    --target "$SITE_PACKAGES" \
    --platform win_amd64 \
    --python-version "${PYTHON_VERSION%.*}" \
    --implementation cp \
    --only-binary=:all: \
    --find-links "$_wheelhouse" \
    --upgrade --no-deps "$WCAG_PIN"

# A wheel built for the wrong platform is the failure mode this whole
# step exists to avoid, and it is invisible until the app runs. pikepdf
# carries a compiled extension, so its tag is the canary.
ls "$SITE_PACKAGES"/pikepdf/_core*.pyd >/dev/null 2>&1 \
    || { echo "ERROR: pikepdf has no Windows extension module in site-packages — a host wheel was installed" >&2; exit 1; }

# pip picks wheels for --platform but evaluates environment markers
# against the interpreter that is running, so every dependency guarded by
# `platform_system == "Windows"` is skipped here without a word. That
# shipped an installer that died at startup on "No time zone found with
# key America/Toronto" — tzlocal's tzdata, never staged.
#
# Re-evaluating the staged metadata under a Windows environment is the
# only way to see what pip decided not to install. Offline, and it needs
# no hand-maintained list: a dependency added upstream next year fails
# the build here instead of on a user's machine.
"$HOST_PYTHON" - "$SITE_PACKAGES" <<'PYEOF'
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

site_packages = Path(sys.argv[1])

WINDOWS = {
    "os_name": "nt",
    "sys_platform": "win32",
    "platform_system": "Windows",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
    "python_version": "3.12",
    "python_full_version": "3.12.8",
    # No extras are requested, so `extra == "..."` guards stay false.
    "extra": "",
}

staged = {
    canonicalize_name(path.name.split("-")[0])
    for path in site_packages.glob("*.dist-info")
}

missing: dict[str, set[str]] = {}
for metadata in site_packages.glob("*.dist-info/METADATA"):
    owner = metadata.parent.name.split("-")[0]
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        if line.strip() == "":
            break
        try:
            requirement = Requirement(line.split(":", 1)[1].strip())
        except Exception:
            continue
        if requirement.marker is not None and not requirement.marker.evaluate(WINDOWS):
            continue
        if canonicalize_name(requirement.name) not in staged:
            missing.setdefault(canonicalize_name(requirement.name), set()).add(owner)

if missing:
    print(
        "ERROR: these are required on Windows but were not staged — pip "
        "evaluated their markers against macOS:",
        file=sys.stderr,
    )
    for name, owners in sorted(missing.items()):
        print(f"  {name}   (required by {', '.join(sorted(owners))})", file=sys.stderr)
    print(
        "Add them to requirements-windows.txt under the Windows-only "
        "section at the bottom.",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(f"Windows dependency closure complete: {len(staged)} distributions")
PYEOF

echo "pip dependencies staged for win_amd64 in $SITE_PACKAGES"

# -------------------------------------------------------
# 3. WeasyPrint native dependencies (GTK)
# -------------------------------------------------------
echo ""
echo "--- Step 3: WeasyPrint native dependencies ---"
GTK_URL="https://github.com/wingtk/gvsbuild/releases/download/${GTK_VERSION}/GTK3_Gvsbuild_${GTK_VERSION}_x64.zip"
GTK_ARCHIVE="$BUILD_DIR/gtk3.zip"
if [ ! -f "$GTK_ARCHIVE" ]; then
    echo "Downloading GTK3 runtime (gvsbuild $GTK_VERSION)..."
    curl -L -o "$GTK_ARCHIVE" "$GTK_URL"
fi
_gtk_actual="$(_sha256 "$GTK_ARCHIVE")"
if [ "$_gtk_actual" != "$GTK_SHA256" ]; then
    echo "ERROR: GTK3 runtime SHA-256 mismatch." >&2
    echo "  expected: $GTK_SHA256" >&2
    echo "  actual:   $_gtk_actual" >&2
    exit 1
fi
unzip -q -o "$GTK_ARCHIVE" -d "$BUILD_DIR/gtk"
# The zip carries bin/ lib/ share/ at its root; cairo and pango live in
# bin/ alongside their dependencies.
[ -d "$BUILD_DIR/gtk/bin" ] \
    || { echo "ERROR: GTK3 archive has no bin/ directory after extraction" >&2; exit 1; }
# gvsbuild uses MSVC naming — cairo-2.dll, not libcairo-2.dll. Both are
# accepted so a future rename does not fail the build for no reason.
#
# Not `ls cairo*.dll libcairo*.dll`: an unmatched glob stays literal, and
# ls fails on a missing argument even when the other one matched. Since
# only one of the two names is ever present, that spelling rejects every
# correct archive.
_have_dll() {
    local found
    for found in "$BUILD_DIR/gtk/bin/$1"*.dll "$BUILD_DIR/gtk/bin/lib$1"*.dll; do
        [ -f "$found" ] && return 0
    done
    return 1
}
_have_dll cairo \
    || { echo "ERROR: no cairo DLL in the GTK runtime — WeasyPrint fails at import, not at render" >&2; exit 1; }
_have_dll pango \
    || { echo "ERROR: no pango DLL in the GTK runtime" >&2; exit 1; }
echo "GTK3 runtime staged at $BUILD_DIR/gtk/"

# -------------------------------------------------------
# 4. Download MongoDB
# -------------------------------------------------------
echo ""
echo "--- Step 4: MongoDB $MONGO_VERSION (windows) ---"
MONGO_URL="https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-${MONGO_VERSION}.zip"
MONGO_ARCHIVE="$BUILD_DIR/mongodb.zip"
if [ ! -f "$MONGO_ARCHIVE" ]; then
    echo "Downloading MongoDB..."
    curl -L -o "$MONGO_ARCHIVE" "$MONGO_URL"
fi
echo "Extracting mongod.exe..."
# The zip has mongodb-win32-*/bin/mongod.exe; -j junks paths so it lands flat.
unzip -j -o "$MONGO_ARCHIVE" '*/bin/mongod.exe' -d "$BUILD_DIR/mongodb/bin"
echo "mongod extracted: $BUILD_DIR/mongodb/bin/mongod.exe"

# -------------------------------------------------------
# 4b. Download ffmpeg + ffprobe (BtbN win64 gpl static)
# -------------------------------------------------------
echo ""
echo "--- Step 4b: ffmpeg + ffprobe (windows static) ---"
FFMPEG_ZIP="$BUILD_DIR/ffmpeg-win.zip"
# The asset name is pinned alongside the tag: a dated BtbN release
# carries several builds, and "master-latest" is a moving target whose
# bytes would never match a recorded hash.
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_WIN_TAG}/${FFMPEG_WIN_ASSET}"
if [ ! -f "$FFMPEG_ZIP" ]; then
    curl -L -o "$FFMPEG_ZIP" "$FFMPEG_URL"
fi
FFMPEG_ACTUAL_SHA="$(_sha256 "$FFMPEG_ZIP")"
if [ "$FFMPEG_ACTUAL_SHA" != "$FFMPEG_WIN_SHA256" ]; then
    echo "ERROR: ffmpeg SHA-256 mismatch" >&2
    echo "  expected: $FFMPEG_WIN_SHA256" >&2
    echo "  actual:   $FFMPEG_ACTUAL_SHA" >&2
    exit 1
fi
# BtbN layout is ffmpeg-*/bin/{ffmpeg,ffprobe}.exe; -j junks paths.
unzip -j -o "$FFMPEG_ZIP" '*/bin/ffmpeg.exe' '*/bin/ffprobe.exe' \
    -d "$BUILD_DIR/ffmpeg/bin"
cat > "$BUILD_DIR/ffmpeg/LICENSE.txt" <<'FFMPEGLIC'
This product bundles FFmpeg (https://ffmpeg.org), a static build from
BtbN/FFmpeg-Builds, licensed under the GNU General Public License v3.
FFmpeg is invoked as a subprocess and is not linked into Auto A11y.
FFMPEGLIC
echo "ffmpeg + ffprobe staged at $BUILD_DIR/ffmpeg/bin/"

# -------------------------------------------------------
# 5. Download Playwright Chromium
# -------------------------------------------------------
echo ""
echo "--- Step 5: Playwright Chromium ---"
# `playwright install` would need the Windows interpreter, so the same
# archives are fetched by URL and laid out the way it lays them out.
# The revisions and the Chrome-for-Testing version come from the
# installed Playwright, so they cannot drift from what the app expects:
# a mismatch makes Playwright re-download at first run, on a machine
# that may have no network.
_pw_info() {
    "$HOST_PYTHON" - "$1" <<'PYEOF'
import json, pathlib, sys, playwright
data = json.loads(
    (pathlib.Path(playwright.__file__).parent
     / "driver" / "package" / "browsers.json").read_text()
)
wanted = sys.argv[1]
for browser in data["browsers"]:
    if browser["name"] == wanted:
        print(browser["revision"])
        break
else:
    raise SystemExit(f"no {wanted} entry in browsers.json")
PYEOF
}

CHROMIUM_REV="$(_pw_info chromium)"
SHELL_REV="$(_pw_info chromium-headless-shell)"
FFMPEG_REV="$(_pw_info ffmpeg)"
CFT_VERSION="$("$HOST_PYTHON" -m playwright install --dry-run chromium \
    | sed -n 's/^Chrome for Testing \([0-9.]*\) .*/\1/p' | head -1)"
[ -n "$CFT_VERSION" ] || { echo "ERROR: could not read the Chrome for Testing version from playwright" >&2; exit 1; }
echo "Chromium ${CFT_VERSION} (rev ${CHROMIUM_REV}), headless shell rev ${SHELL_REV}, ffmpeg rev ${FFMPEG_REV}"

_stage_browser() {  # url, destination dir
    local url="$1" dest="$2" archive
    archive="$BUILD_DIR/$(basename "$url")"
    [ -f "$archive" ] || curl -L -o "$archive" "$url"
    mkdir -p "$dest"
    unzip -q -o "$archive" -d "$dest"
    # Playwright treats a browser directory without these as an aborted
    # download and fetches it again at run time.
    : > "$dest/INSTALLATION_COMPLETE"
    : > "$dest/DEPENDENCIES_VALIDATED"
}

CFT_BASE="https://cdn.playwright.dev/chrome-for-testing-public/${CFT_VERSION}/win64"
PW_BASE="https://cdn.playwright.dev/dbazure/download/playwright/builds"

_stage_browser "${CFT_BASE}/chrome-win64.zip" \
    "$BUILD_DIR/chromium/chromium-${CHROMIUM_REV}"
_stage_browser "${CFT_BASE}/chrome-headless-shell-win64.zip" \
    "$BUILD_DIR/chromium/chromium_headless_shell-${SHELL_REV}"
_stage_browser "${PW_BASE}/ffmpeg/${FFMPEG_REV}/ffmpeg-win64.zip" \
    "$BUILD_DIR/chromium/ffmpeg-${FFMPEG_REV}"

[ -f "$BUILD_DIR/chromium/chromium-${CHROMIUM_REV}/chrome-win64/chrome.exe" ] \
    || { echo "ERROR: chrome.exe missing — Playwright will not find the bundled browser" >&2; exit 1; }
echo "Chromium staged at $BUILD_DIR/chromium/"

# -------------------------------------------------------
# 6. Copy application source
# -------------------------------------------------------
echo ""
echo "--- Step 6: Copy application source ---"
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
    "$PROJECT_DIR/auto_a11y" "$BUILD_DIR/app/"

# The callouts/branding stage reads this PNG at runtime — resolved relative
# to auto_a11y/audio/callouts.py (_asset_path), NOT pip-installed, so it
# ships only via the rsync above. A missing/empty copy makes the annotated
# "callouts" video render without the AccessLabs title-card logo + watermark
# (or abort the render entirely). Fail at copy time, not on the user's machine.
_logo_asset="$BUILD_DIR/app/auto_a11y/audio/assets/accesslabs_logo.png"
[ -s "$_logo_asset" ] \
    || { echo "ERROR: callouts logo asset missing/empty after copy: $_logo_asset" >&2; exit 1; }

# Stamp the git commit so the running app can report which build it is — the
# bundle carries no .git, so auto_a11y/build_info.py reads this file. Falls
# back to "unknown" if the build host isn't a git checkout.
git -C "$PROJECT_DIR" rev-parse --short HEAD > "$BUILD_DIR/app/auto_a11y/BUILD_COMMIT" 2>/dev/null \
    || echo "unknown" > "$BUILD_DIR/app/auto_a11y/BUILD_COMMIT"
echo "Stamped build commit: $(cat "$BUILD_DIR/app/auto_a11y/BUILD_COMMIT")"

rsync -a --exclude='__pycache__' --exclude='*.pyc' \
    "$PROJECT_DIR/Fixtures" "$BUILD_DIR/app/"
for f in config.py run.py wsgi.py; do
    [ -f "$PROJECT_DIR/$f" ] && cp "$PROJECT_DIR/$f" "$BUILD_DIR/app/"
done
[ -f "$PROJECT_DIR/.env.example" ] && cp "$PROJECT_DIR/.env.example" "$BUILD_DIR/app/"
echo "Application source copied."

# -------------------------------------------------------
# 7. Package with electron-builder
# -------------------------------------------------------
echo ""
echo "--- Step 7: Electron packaging ---"
cd "$ELECTRON_DIR"
if [ ! -d "node_modules" ]; then
    npm install
fi
cat > "$ELECTRON_DIR/build-config.json" <<BUILDCFG
{
  "appId": "com.cnib.auto-a11y",
  "productName": "Auto A11y",
  "directories": {
    "output": "dist"
  },
  "extraResources": [
    { "from": "$BUILD_DIR/python", "to": "python" },
    { "from": "$BUILD_DIR/app", "to": "app" },
    { "from": "$BUILD_DIR/mongodb", "to": "mongodb" },
    { "from": "$BUILD_DIR/chromium", "to": "chromium" },
    { "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" },
    { "from": "$BUILD_DIR/gtk", "to": "gtk" }
  ],
  "win": {
    "target": [{ "target": "nsis", "arch": ["x64"] }]
  }
}
BUILDCFG
echo "Generated build-config.json"

# --x64 is not optional. electron-builder defaults to the host
# architecture, so on an Apple Silicon Mac it produces an arm64 Windows
# shell — around an x86_64 Python, an x86_64 mongod and an x86_64
# Chromium. It builds, installs, and fails at launch.
#
# Wine is not installed by hand: electron-builder fetches its own
# (wine-4.0.1-mac) the first time it builds a Windows target.
npx electron-builder --win --x64 --config build-config.json

echo ""
echo "=== Build complete ==="
echo "Output: $ELECTRON_DIR/dist/"
ls -lh "$ELECTRON_DIR/dist/"*.exe 2>/dev/null || echo "(Check $ELECTRON_DIR/dist/ for output)"
echo ""
echo "Built on macOS for x86_64 Windows. Nothing here has run a Windows"
echo "binary, so install it in the VM and check it launches before"
echo "trusting it."
