#!/usr/bin/env bash
# Render build script — runs on every deploy
set -o errexit

echo "==> Installing system packages..."
apt-get update -qq
# WeasyPrint dependencies + Chromium runtime dependencies
apt-get install -y -qq --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 shared-mime-info \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 \
    libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libatspi2.0-0 \
    fonts-liberation xdg-utils

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Running database setup..."
python -c "
from config import config
from auto_a11y.core.database import Database
db = Database(config.MONGODB_URI, config.DATABASE_NAME)
if db.test_connection():
    db.create_indexes()
    print('Database indexes created.')
else:
    print('WARNING: Could not connect to database during build.')
"

# Install Playwright + Chromium only when BROWSER_MODE is "local"
if [ "${BROWSER_MODE:-local}" = "local" ]; then
    echo "==> Installing Playwright Chromium (BROWSER_MODE=local)..."
    # Store browsers in a known, persistent location
    export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.playwright
    python -m playwright install --with-deps chromium

    echo "==> Chromium install diagnostics:"
    echo "    PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH"
    ls -laR "$PLAYWRIGHT_BROWSERS_PATH" 2>/dev/null | head -40 || echo "    WARNING: $PLAYWRIGHT_BROWSERS_PATH not found!"

    # Verify the binary is executable
    CHROME_BIN=$(find "$PLAYWRIGHT_BROWSERS_PATH" -name "chrome" -type f 2>/dev/null | head -1)
    if [ -n "$CHROME_BIN" ]; then
        echo "    Chromium binary: $CHROME_BIN"
        echo "    Executable: $(test -x "$CHROME_BIN" && echo 'yes' || echo 'NO')"
        echo "    Shared libs check:"
        ldd "$CHROME_BIN" 2>/dev/null | grep "not found" || echo "    All shared libraries found."
    else
        echo "    WARNING: Chromium binary not found under $PLAYWRIGHT_BROWSERS_PATH"
    fi
else
    echo "==> Skipping Playwright install (BROWSER_MODE=${BROWSER_MODE})"
fi

echo "==> Build complete."
