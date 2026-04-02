#!/bin/bash
# Build Auto A11y desktop app for macOS (arm64 / x86_64)
# Creates a DMG in electron/dist/
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build/staging"
ELECTRON_DIR="$PROJECT_DIR/electron"

# Versions
PYTHON_VERSION="3.12.8"
PYTHON_BUILD_TAG="20250106"
MONGO_VERSION="7.0.17"

# Detect architecture
ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
    PYTHON_ARCH="aarch64-apple-darwin"
    MONGO_ARCH="arm64"
elif [ "$ARCH" = "x86_64" ]; then
    PYTHON_ARCH="x86_64-apple-darwin"
    MONGO_ARCH="x86_64"
else
    echo "Unsupported architecture: $ARCH"
    exit 1
fi

echo "=== Auto A11y macOS Build ($ARCH) ==="
echo "Project: $PROJECT_DIR"
echo "Build staging: $BUILD_DIR"

# Clean previous build
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{python,app,mongodb/bin,chromium}

# -------------------------------------------------------
# 1. Download portable Python
# -------------------------------------------------------
echo ""
echo "--- Step 1: Portable Python $PYTHON_VERSION ($PYTHON_ARCH) ---"
PYTHON_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${PYTHON_ARCH}-install_only.tar.gz"
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
# 3. Install & bundle WeasyPrint native dependencies
# -------------------------------------------------------
echo ""
echo "--- Step 3: WeasyPrint native dependencies ---"

# Install via Homebrew (idempotent)
brew install cairo pango gdk-pixbuf gobject-introspection libffi

# Bundle dylibs into the staging directory so the app works without Homebrew
DYLIB_DIR="$BUILD_DIR/python/lib/weasyprint_libs"
mkdir -p "$DYLIB_DIR"

# Determine Homebrew prefix (different on ARM vs Intel)
BREW_PREFIX="$(brew --prefix)"

# Collect the core dylibs we need
DYLIB_NAMES=(
    libcairo.2.dylib
    libpango-1.0.0.dylib
    libpangocairo-1.0.0.dylib
    libpangoft2-1.0.0.dylib
    libgdk_pixbuf-2.0.0.dylib
    libgobject-2.0.0.dylib
    libglib-2.0.0.dylib
    libgio-2.0.0.dylib
    libgmodule-2.0.0.dylib
    libfontconfig.1.dylib
    libfreetype.6.dylib
    libharfbuzz.0.dylib
    libfribidi.0.dylib
    libpixman-1.0.dylib
    libpng16.16.dylib
    libintl.8.dylib
    libffi.8.dylib
    libgthread-2.0.0.dylib
)

echo "Collecting dylibs from Homebrew..."

# Recursive function to collect a dylib and its Homebrew dependencies
collect_dylib() {
    local src="$1"
    local name
    name="$(basename "$src")"

    # Skip if already collected
    if [ -f "$DYLIB_DIR/$name" ]; then
        return
    fi

    # Only collect dylibs that live under Homebrew prefix
    case "$src" in
        "$BREW_PREFIX"*) ;;
        *) return ;;
    esac

    cp "$src" "$DYLIB_DIR/$name"

    # Find Homebrew-based dependencies of this dylib
    otool -L "$DYLIB_DIR/$name" 2>/dev/null | tail -n +2 | awk '{print $1}' | while read -r dep; do
        case "$dep" in
            "$BREW_PREFIX"*)
                collect_dylib "$dep"
                ;;
        esac
    done
}

# Seed collection with the named dylibs
for name in "${DYLIB_NAMES[@]}"; do
    found="$(find "$BREW_PREFIX/lib" "$BREW_PREFIX/opt" -name "$name" 2>/dev/null | head -1)"
    if [ -n "$found" ]; then
        collect_dylib "$found"
    fi
done

# Rewrite install names so each dylib references siblings via @loader_path
echo "Rewriting dylib install names..."
for dylib in "$DYLIB_DIR"/*.dylib; do
    name="$(basename "$dylib")"

    # Change the dylib's own install name
    install_name_tool -id "@loader_path/$name" "$dylib" 2>/dev/null || true

    # Rewrite references to other Homebrew dylibs
    otool -L "$dylib" 2>/dev/null | tail -n +2 | awk '{print $1}' | while read -r dep; do
        dep_name="$(basename "$dep")"
        if [ -f "$DYLIB_DIR/$dep_name" ] && [ "$dep" != "@loader_path/$dep_name" ]; then
            install_name_tool -change "$dep" "@loader_path/$dep_name" "$dylib" 2>/dev/null || true
        fi
    done
done

echo "Bundled $(ls "$DYLIB_DIR"/*.dylib 2>/dev/null | wc -l | tr -d ' ') dylibs into $DYLIB_DIR"

# Create a wrapper script that sets DYLD_LIBRARY_PATH before running Python.
# The electron process-manager will invoke this instead of python3.12 directly.
cat > "$BUILD_DIR/python/bin/python3.12-wrapper" <<'WRAPPER'
#!/bin/bash
# Wrapper that ensures WeasyPrint can find its native libraries
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export DYLD_LIBRARY_PATH="$SCRIPT_DIR/../lib/weasyprint_libs${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
exec "$SCRIPT_DIR/python3.12" "$@"
WRAPPER
chmod +x "$BUILD_DIR/python/bin/python3.12-wrapper"

# -------------------------------------------------------
# 4. Download MongoDB
# -------------------------------------------------------
echo ""
echo "--- Step 4: MongoDB $MONGO_VERSION ($MONGO_ARCH) ---"
MONGO_URL="https://fastdl.mongodb.org/osx/mongodb-macos-${MONGO_ARCH}-${MONGO_VERSION}.tgz"
MONGO_ARCHIVE="$BUILD_DIR/mongodb.tgz"

if [ ! -f "$MONGO_ARCHIVE" ]; then
    echo "Downloading MongoDB..."
    curl -L -o "$MONGO_ARCHIVE" "$MONGO_URL"
fi

echo "Extracting mongod binary..."
tar -xzf "$MONGO_ARCHIVE" -C "$BUILD_DIR" --strip-components=1 --include="*/bin/mongod"
mv "$BUILD_DIR/bin/mongod" "$BUILD_DIR/mongodb/bin/mongod"
rm -rf "$BUILD_DIR/bin"
echo "mongod extracted: $BUILD_DIR/mongodb/bin/mongod"

# -------------------------------------------------------
# 5. Download Playwright Chromium
# -------------------------------------------------------
echo ""
echo "--- Step 5: Playwright Chromium ---"
export PLAYWRIGHT_BROWSERS_PATH="$BUILD_DIR/chromium"
$PORTABLE_PYTHON -m playwright install chromium chromium-headless-shell
echo "Chromium installed to $BUILD_DIR/chromium"

# -------------------------------------------------------
# 6. Copy application source
# -------------------------------------------------------
echo ""
echo "--- Step 6: Copy application source ---"
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

# Copy requirements.txt (needed by the app at runtime for version checks)
[ -f "$PROJECT_DIR/requirements.txt" ] && cp "$PROJECT_DIR/requirements.txt" "$BUILD_DIR/app/"

echo "Application source copied."

# -------------------------------------------------------
# 7. Package with electron-builder
# -------------------------------------------------------
echo ""
echo "--- Step 7: Electron packaging ---"
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
    { "from": "$BUILD_DIR/chromium", "to": "chromium" }
  ],
  "mac": {
    "target": "dmg",
    "category": "public.app-category.developer-tools"
  },
  "dmg": {
    "title": "Auto A11y"
  }
}
BUILDCFG

echo "Generated build-config.json"

# Run electron-builder
npx electron-builder --mac --config build-config.json

echo ""
echo "=== Build complete ==="
echo "Output: $ELECTRON_DIR/dist/"
ls -lh "$ELECTRON_DIR/dist/"*.dmg 2>/dev/null || echo "(Check $ELECTRON_DIR/dist/ for output)"
