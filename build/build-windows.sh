#!/bin/bash
# Build Auto A11y desktop app for Windows (x86_64)
# Creates an NSIS installer in electron/dist/
#
# STATUS: runs to completion on a Windows host. Run it from Git Bash on
# Windows — a VM is fine — not from macOS or Linux: steps 2 and 5 execute
# the bundled python.exe, and electron-builder\'s NSIS target needs a
# Windows host or wine.
#
# Cross-building from macOS is close to possible: 135 of the 136 pinned
# requirements publish win_amd64 wheels, so pip can populate
# site-packages with --platform win_amd64 --only-binary=:all: without
# running python.exe. What stops it is Playwright\'s installer and
# electron-builder. Not worth the complexity while a Windows VM exists,
# and a build nobody has launched on Windows is a guess anyway.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build/staging"
ELECTRON_DIR="$PROJECT_DIR/electron"

# Versions
PYTHON_VERSION="3.12.8"
PYTHON_BUILD_TAG="20250106"
MONGO_VERSION="7.0.17"
# ffmpeg: BtbN FFmpeg-Builds win64 gpl static. Pin to a DATED release tag
# (not "latest") and capture its real SHA-256 on the build host via
#   curl -L <url> | sha256sum
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
echo "Python extracted: $PORTABLE_PYTHON"

# -------------------------------------------------------
# 2. Install pip dependencies
# -------------------------------------------------------
echo ""
echo "--- Step 2: Install pip dependencies ---"
"$PORTABLE_PYTHON" -m pip install --upgrade pip
"$PORTABLE_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
echo "pip dependencies installed."

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
_gtk_actual="$(sha256sum "$GTK_ARCHIVE" | cut -d" " -f1)"
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
ls "$BUILD_DIR/gtk/bin"/cairo*.dll "$BUILD_DIR/gtk/bin"/libcairo*.dll >/dev/null 2>&1 \
    || { echo "ERROR: no cairo DLL in the GTK runtime — WeasyPrint fails at import, not at render" >&2; exit 1; }
ls "$BUILD_DIR/gtk/bin"/pango*.dll "$BUILD_DIR/gtk/bin"/libpango*.dll >/dev/null 2>&1 \
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
FFMPEG_ACTUAL_SHA="$(sha256sum "$FFMPEG_ZIP" | awk '{print $1}')"
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
PLAYWRIGHT_BROWSERS_PATH="$BUILD_DIR/chromium" \
    "$PORTABLE_PYTHON" -m playwright install chromium chromium-headless-shell
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
    "target": "nsis"
  }
}
BUILDCFG
echo "Generated build-config.json"

npx electron-builder --win --config build-config.json

echo ""
echo "=== Build complete ==="
echo "Output: $ELECTRON_DIR/dist/"
ls -lh "$ELECTRON_DIR/dist/"*.exe 2>/dev/null || echo "(Check $ELECTRON_DIR/dist/ for output)"
