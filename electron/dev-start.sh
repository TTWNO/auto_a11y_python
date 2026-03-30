#!/bin/bash
# Development launcher for Electron shell.
# Uses system mongod and project's Python venv instead of bundled binaries.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Starting Auto A11y in development mode..."
echo "Project: $PROJECT_DIR"
echo "Electron: $SCRIPT_DIR"

cd "$SCRIPT_DIR"
npx electron . --dev
