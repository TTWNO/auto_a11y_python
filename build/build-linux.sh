#!/bin/bash
# Build Auto A11y desktop app for Linux (x86_64)
# Creates an AppImage in electron/dist/
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build/staging"
ELECTRON_DIR="$PROJECT_DIR/electron"

# Versions
PYTHON_VERSION="3.12.8"
PYTHON_BUILD_TAG="20250106"
MONGO_VERSION="7.0.17"
# ffmpeg: johnvansickle.com static amd64 build. Pinned + SHA-verified.
# Prefer a dated archive; capture its real SHA-256 on the build host via
#   curl -L <url> | sha256sum
# The build FAILS on mismatch. The tarball ships BOTH ffmpeg + ffprobe.
FFMPEG_LINUX_VERSION="release"
FFMPEG_LINUX_SHA256="REPLACE_WITH_REAL_SHA256"

echo "=== Auto A11y Linux Build ==="
echo "Project: $PROJECT_DIR"
echo "Build staging: $BUILD_DIR"

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{python,app,mongodb/bin,chromium,ffmpeg/bin}

# -------------------------------------------------------
# 1. Download portable Python
# -------------------------------------------------------
echo ""
echo "--- Step 1: Portable Python $PYTHON_VERSION ---"
PYTHON_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_ARCHIVE="$BUILD_DIR/python-standalone.tar.gz"

if [ ! -f "$PYTHON_ARCHIVE" ]; then
    echo "Downloading portable Python..."
    curl -L -o "$PYTHON_ARCHIVE" "$PYTHON_URL"
fi

echo "Extracting Python..."
tar -xzf "$PYTHON_ARCHIVE" -C "$BUILD_DIR/python" --strip-components=1
PORTABLE_PYTHON="$BUILD_DIR/python/bin/python3.12"
echo "Python extracted: $PORTABLE_PYTHON"
$PORTABLE_PYTHON --version

# -------------------------------------------------------
# 2. Install pip dependencies
# -------------------------------------------------------
echo ""
echo "--- Step 2: Install pip dependencies ---"
$PORTABLE_PYTHON -m ensurepip --upgrade 2>/dev/null || true
$PORTABLE_PYTHON -m pip install --upgrade pip --quiet
$PORTABLE_PYTHON -m pip install -r "$PROJECT_DIR/requirements.txt" --quiet
echo "Dependencies installed."

# -------------------------------------------------------
# 3. Download MongoDB
# -------------------------------------------------------
echo ""
echo "--- Step 3: MongoDB $MONGO_VERSION ---"
MONGO_URL="https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2204-${MONGO_VERSION}.tgz"
MONGO_ARCHIVE="$BUILD_DIR/mongodb.tgz"

if [ ! -f "$MONGO_ARCHIVE" ]; then
    echo "Downloading MongoDB..."
    curl -L -o "$MONGO_ARCHIVE" "$MONGO_URL"
fi

echo "Extracting mongod binary..."
tar -xzf "$MONGO_ARCHIVE" -C "$BUILD_DIR" --strip-components=1 --wildcards "*/bin/mongod"
mv "$BUILD_DIR/bin/mongod" "$BUILD_DIR/mongodb/bin/mongod"
rm -rf "$BUILD_DIR/bin"
echo "mongod extracted: $BUILD_DIR/mongodb/bin/mongod"

# -------------------------------------------------------
# 4. Download Playwright Chromium
# -------------------------------------------------------
echo ""
echo "--- Step 4: Playwright Chromium ---"
export PLAYWRIGHT_BROWSERS_PATH="$BUILD_DIR/chromium"
$PORTABLE_PYTHON -m playwright install chromium chromium-headless-shell
echo "Chromium installed to $BUILD_DIR/chromium"

# -------------------------------------------------------
# 4b. Download ffmpeg + ffprobe (static, for audioA11y video pipeline)
# -------------------------------------------------------
echo ""
echo "--- Step 4b: ffmpeg + ffprobe (linux static) ---"
FFMPEG_TARBALL="$BUILD_DIR/ffmpeg-linux.tar.xz"
FFMPEG_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-${FFMPEG_LINUX_VERSION}-amd64-static.tar.xz"
curl -L -o "$FFMPEG_TARBALL" "$FFMPEG_URL"
FFMPEG_ACTUAL_SHA="$(sha256sum "$FFMPEG_TARBALL" | awk '{print $1}')"
if [ "$FFMPEG_ACTUAL_SHA" != "$FFMPEG_LINUX_SHA256" ]; then
    echo "ERROR: ffmpeg SHA-256 mismatch" >&2
    echo "  expected: $FFMPEG_LINUX_SHA256" >&2
    echo "  actual:   $FFMPEG_ACTUAL_SHA" >&2
    exit 1
fi
# The tarball has one top-level versioned dir containing ffmpeg + ffprobe.
tar -xJf "$FFMPEG_TARBALL" -C "$BUILD_DIR/ffmpeg/bin" --strip-components=1 \
    --wildcards '*/ffmpeg' '*/ffprobe'
chmod +x "$BUILD_DIR/ffmpeg/bin/ffmpeg" "$BUILD_DIR/ffmpeg/bin/ffprobe"
"$BUILD_DIR/ffmpeg/bin/ffmpeg" -version | head -1
cat > "$BUILD_DIR/ffmpeg/LICENSE.txt" <<'FFMPEGLIC'
This product bundles FFmpeg (https://ffmpeg.org), a static build from
johnvansickle.com, licensed under the GNU General Public License v3.
FFmpeg is invoked as a subprocess and is not linked into Auto A11y.
FFMPEGLIC
echo "ffmpeg + ffprobe staged at $BUILD_DIR/ffmpeg/bin/"

# -------------------------------------------------------
# 5. Copy application source
# -------------------------------------------------------
echo ""
echo "--- Step 5: Copy application source ---"
# Copy essential files/dirs only
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
    "$PROJECT_DIR/auto_a11y" "$BUILD_DIR/app/"
rsync -a --exclude='__pycache__' --exclude='*.pyc' \
    "$PROJECT_DIR/Fixtures" "$BUILD_DIR/app/"

# Copy top-level Python files
for f in config.py run.py wsgi.py; do
    [ -f "$PROJECT_DIR/$f" ] && cp "$PROJECT_DIR/$f" "$BUILD_DIR/app/"
done

# Copy .env.example if it exists (not .env itself — that has secrets)
[ -f "$PROJECT_DIR/.env.example" ] && cp "$PROJECT_DIR/.env.example" "$BUILD_DIR/app/"

echo "Application source copied."

# -------------------------------------------------------
# 6. Package with electron-builder
# -------------------------------------------------------
echo ""
echo "--- Step 6: Electron packaging ---"
cd "$ELECTRON_DIR"

# Ensure node_modules are installed
if [ ! -d "node_modules" ]; then
    npm install
fi

# Generate build config that points extraResources at the staging directory
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
  "linux": {
    "target": "AppImage",
    "category": "Utility"
  }
}
BUILDCFG

echo "Generated build-config.json"

# Run electron-builder with the generated config
npx electron-builder --linux --config build-config.json

echo ""
echo "=== Build complete ==="
echo "Output: $ELECTRON_DIR/dist/"
ls -lh "$ELECTRON_DIR/dist/"*.AppImage 2>/dev/null || echo "(Check $ELECTRON_DIR/dist/ for output)"
