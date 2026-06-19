#!/bin/bash
# Build Auto A11y desktop app for Windows (x86_64)
# Creates an NSIS installer in electron/dist/
#
# STATUS: prep-only skeleton. The ffmpeg/ffprobe + mongod + Chromium bundling
# is complete and correct. The portable-Python + WeasyPrint-native-deps story
# on Windows is NOT solved here (see TODO_WINDOWS markers) — Windows uses a
# different native-GTK stack than the macOS (brew) / Linux (apt) builds.
# This script is NOT run in CI. Run it manually under Git Bash / WSL on a
# Windows host (or a windows-latest runner) once the TODOs are resolved.
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
FFMPEG_WIN_TAG="REPLACE_WITH_DATED_BTBN_TAG"   # e.g. autobuild-2026-05-01-12-31
FFMPEG_WIN_SHA256="REPLACE_WITH_REAL_SHA256"

echo "=== Auto A11y Windows Build ==="
echo "Project: $PROJECT_DIR"
echo "Build staging: $BUILD_DIR"

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{python,app,mongodb/bin,chromium,ffmpeg/bin}

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
# (no bin/ dir). electron/process-manager.js currently resolves the Unix
# layout (python/bin/python3.12); the Windows path resolution is a separate
# follow-up — see TODO_WINDOWS_PYTHON below.
PORTABLE_PYTHON="$BUILD_DIR/python/python.exe"
echo "Python extracted: $PORTABLE_PYTHON"

# -------------------------------------------------------
# 2. Install pip dependencies
# -------------------------------------------------------
echo ""
echo "--- Step 2: Install pip dependencies ---"
# TODO_WINDOWS_PYTHON: running python.exe from a Linux/WSL build host needs
# either a Windows runner or wine. On a real windows-latest runner this is:
#   "$PORTABLE_PYTHON" -m pip install --upgrade pip
#   "$PORTABLE_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"
# torch + pyannote.audio + deepgram-sdk all ship Windows wheels, so the audio
# pipeline deps install cleanly; WeasyPrint's native GTK stack is the open
# problem (see Step 3).
echo "TODO_WINDOWS_PYTHON: pip install step is a no-op skeleton; wire on a Windows runner."

# -------------------------------------------------------
# 3. WeasyPrint native dependencies (GTK)
# -------------------------------------------------------
echo ""
echo "--- Step 3: WeasyPrint native dependencies ---"
# TODO_WINDOWS_WEASYPRINT: macOS uses brew (cairo/pango/gdk-pixbuf), Linux uses
# apt + bundled .so. Windows needs the GTK3 runtime bundled alongside the app
# (e.g. the gvsbuild artifacts) and on PATH for WeasyPrint. Out of scope for
# the ffmpeg-bundling sub-project; tracked as a Windows-build follow-up.
echo "TODO_WINDOWS_WEASYPRINT: GTK runtime bundling not yet implemented."

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
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_WIN_TAG}/ffmpeg-master-latest-win64-gpl.zip"
curl -L -o "$FFMPEG_ZIP" "$FFMPEG_URL"
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
# TODO_WINDOWS_PYTHON: needs the Windows python.exe to run playwright install.
# On a Windows runner:
#   PLAYWRIGHT_BROWSERS_PATH="$BUILD_DIR/chromium" "$PORTABLE_PYTHON" -m playwright install chromium chromium-headless-shell
echo "TODO_WINDOWS_PYTHON: playwright install step is a no-op skeleton."

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
    { "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" }
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
