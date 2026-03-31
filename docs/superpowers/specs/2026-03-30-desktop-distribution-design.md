# Desktop Distribution Design — Auto A11y Python

## Overview

Package Auto A11y Python as a distributable desktop application using Electron as a thin wrapper around the existing Flask web UI, with embedded Python runtime and sidecar processes for MongoDB and Chromium. All four major components (server, database, browser, LLM) are independently switchable between internal (bundled) and external (remote) modes via an in-app settings panel.

## Requirements

### Target Users
- Internal team members (developers/QA) comfortable with configuration
- Non-technical staff (accessibility auditors, project managers) needing zero-setup
- External clients/partners receiving the app as a deliverable
- Mix of power users and non-technical users

### Platform Support
- Windows (NSIS installer)
- macOS (DMG)
- Linux (AppImage + .deb)

### Distribution Strategy
- Versioned installers for initial distribution
- Auto-update capability (electron-updater checking GitHub Releases or internal server)

### Operational Requirements
- Must work fully air-gapped (offline/no internet)
- Ship everything in one installer (~1-1.5GB acceptable)
- Server starts/stops with the app window (no background service)
- Enterprise-scale data (tens of GBs)
- Default single-user, optional authentication for shared installations

### Component Flexibility
Each component switchable between internal and external via settings panel:

| Component | Internal (Default) | External Option |
|-----------|-------------------|-----------------|
| Database | Bundled mongod binary | Remote MongoDB URI |
| Server | Embedded Python + Flask | Remote auto_a11y URL |
| Browser | Bundled Playwright Chromium | Remote Playwright endpoint or system Chrome |
| LLM | Off (default) | Claude API key or Ollama endpoint URL |

## Architecture

### Approach: Embedded Python + Sidecar Processes

Ship a portable Python distribution (python-build-standalone) with the auto_a11y source code, rather than freezing with PyInstaller. Electron manages all process lifecycles.

**Why this approach over PyInstaller:**
- WeasyPrint depends on Cairo, Pango, GdkPixbuf — system-level C libraries that PyInstaller struggles to bundle across platforms
- Playwright's browser binaries and integration don't freeze cleanly
- Source code remains debuggable for internal distribution
- Build pipeline is dramatically simpler — no freeze step, no hidden import hunting
- Source visibility is acceptable for internal distribution

**Why this approach over Docker-in-Electron:**
- No Docker/Podman dependency (big ask for non-technical users)
- No Docker Desktop licensing costs
- Faster startup, smaller bundle
- Air-gapped deployment is simpler without container registries

### Runtime Components

**Electron Main Process** — the orchestrator:
- Creates BrowserWindow that loads the Flask UI
- Spawns and monitors child processes (Python, mongod)
- Reads settings.json to determine internal vs external components
- Handles native features: auto-update, error dialogs, app lifecycle
- Written in JavaScript

**Embedded Python + Flask** — the application:
- Portable Python 3.12 from python-build-standalone (~45MB compressed)
- All pip dependencies pre-installed in site-packages/
- Entire auto_a11y source code + Fixtures shipped as-is
- Electron spawns `python run.py` with injected environment variables
- Flask serves on localhost:5001

**Sidecar MongoDB** — the database:
- Platform-specific mongod binary (~100-300MB)
- Data stored in user data directory (persists across updates)
- Electron starts mongod with `--dbpath` pointing to user data
- Graceful shutdown via `mongod --shutdown` (cross-platform, avoids SIGTERM issues on Windows)

**Bundled Chromium** — the scanner:
- Playwright's Chromium pre-downloaded during build (~250MB)
- `PLAYWRIGHT_BROWSERS_PATH` env var points to bundle location
- Spawned per-scan by Playwright (managed by Flask, not Electron)

**LLM Integration** — AI analysis:
- Claude API: primary path, requires API key in settings
- Ollama: nice-to-have local alternative, text-only fallback
- Off: default mode, skip AI analysis entirely

### Process Lifecycle

#### Startup Sequence (~5-15 seconds)

1. **User launches app** → Electron main process starts, shows splash screen with progress
2. **Read config** → load settings.json from userData/, determine internal vs external
3. **Start MongoDB** (if internal) → spawn `mongod --dbpath userData/mongodb/ --port <port>`, poll until accepting connections (max 15s timeout). If port is in use, auto-increment (27017 → 27018 → 27019) up to 3 attempts.
4. **Start Flask** (if internal) → spawn `python run.py --port <port>` with injected env vars (MONGODB_URI, PLAYWRIGHT_BROWSERS_PATH, DESKTOP_MODE, USER_DATA_DIR, RUN_AI_ANALYSIS=False unless Claude key is configured), poll `http://localhost:<port>/health` until ready (max 30s). If port is in use, auto-increment (5001 → 5002 → 5003) up to 3 attempts.
5. **Open BrowserWindow** → load Flask URL, hide splash screen

#### Shutdown Sequence (graceful, ordered)

1. User closes window or quits app → Electron intercepts `before-quit`
2. **Flask shutdown** — send HTTP POST to `/shutdown` endpoint (a new minimal route that calls `os._exit(0)`). This is cross-platform and avoids SIGTERM which doesn't work on Windows. Note: `werkzeug.server.shutdown()` was removed in Werkzeug 2.x and the project uses Werkzeug 3.1.7, so `os._exit(0)` is the correct approach. Since this endpoint is only called by Electron (trusted caller over localhost), the abrupt exit is acceptable — Electron waits up to 5 seconds then force-kills if needed.
3. **MongoDB shutdown** — run `mongod --shutdown --dbpath <path>`, wait up to 10 seconds (ensures data integrity, works on all platforms)
4. Force-kill any remaining child processes (via `child.kill()` / `taskkill` on Windows), app exits

#### Error Recovery

| Scenario | Handling |
|----------|----------|
| MongoDB won't start | Auto-increment port (27017 → 27018 → 27019) up to 3 attempts. If still failing, show error dialog: "Database failed to start." Offer: [Open Settings] [Retry] [Quit] |
| Flask won't start | Auto-increment port (5001 → 5002 → 5003) up to 3 attempts. If still failing, show error dialog with Python stderr. Offer: [View Logs] [Retry] [Quit] |
| Process crash mid-use | Monitor child processes. On unexpected exit: "The server stopped unexpectedly." Offer: [Restart] [View Logs] [Quit] |
| Stale MongoDB lock | On startup, check for mongod.lock. If stale (no running process), clean up and start fresh |

### External Server Mode

When the user switches Server to "External" and provides a remote auto_a11y URL:
- Electron does NOT spawn Python or MongoDB
- BrowserWindow loads the remote URL directly
- Settings panel remains accessible via Electron's native menu (opens a local settings-only page)
- The desktop app becomes a thin native wrapper around the remote instance

## Directory Structure

### App Bundle (ships with installer, read-only)

```
auto-a11y/
├── electron/
│   ├── main.js              # Electron main process
│   ├── preload.js            # IPC bridge (if needed)
│   ├── splash.html           # Startup screen with progress
│   ├── process-manager.js    # Child process orchestrator
│   ├── settings-manager.js   # Read/write settings.json
│   ├── updater.js            # Auto-update logic
│   └── package.json          # Electron deps + builder config
│
├── python/
│   ├── bin/python3.12        # Portable Python binary
│   ├── lib/python3.12/
│   │   └── site-packages/    # All pip dependencies
│   └── share/                # Python standard data
│
├── app/
│   ├── auto_a11y/            # Source code (unchanged)
│   ├── Fixtures/             # Test fixtures (~900 HTML files)
│   ├── config.py             # Configuration
│   ├── run.py                # Entry point
│   └── ...                   # All other source files
│
├── mongodb/
│   └── bin/mongod            # Platform-specific binary
│
├── chromium/                  # Playwright Chromium files
│
└── resources/
    └── icon.png              # App icon (per platform)
```

### User Data (persists across updates, writable)

```
# Linux: ~/.config/auto-a11y/
# macOS: ~/Library/Application Support/auto-a11y/
# Windows: %APPDATA%/auto-a11y/

userData/
├── settings.json        # Component config (see Settings Schema below)
├── mongodb/
│   └── data/            # MongoDB data files
├── logs/
│   ├── electron.log     # Electron process log
│   ├── flask.log        # Python/Flask output
│   └── mongod.log       # MongoDB log
├── reports/             # Generated reports
├── screenshots/         # Test screenshots
├── temp/                # Temporary files
└── .env                 # Optional env var overrides
```

### Settings Schema (settings.json)

```json
{
  "database": {
    "mode": "internal",
    "uri": "mongodb://localhost:27017/auto_a11y",
    "internal_port": 27017
  },
  "server": {
    "mode": "internal",
    "url": "http://localhost:5001",
    "internal_port": 5001
  },
  "browser": {
    "mode": "internal",
    "playwright_endpoint": "",
    "system_chrome_path": ""
  },
  "llm": {
    "mode": "off",
    "claude_api_key": "",
    "claude_model": "claude-opus-4-20250514",
    "ollama_url": "",
    "ollama_model": ""
  },
  "auth": {
    "enabled": false
  },
  "updates": {
    "auto_check": true,
    "server_url": ""
  }
}
```

## Settings Panel

A new Flask route (`/settings/components`) added to the existing admin UI, matching the current Bootstrap 5 styling.

### UI Design

Each component gets a card with:
- Name and description
- Internal/External toggle button group
- Live status indicator (green = running, grey = disabled, red = error)
- When switched to "External," the card expands to show connection fields with "Test Connection" and "Save" buttons

### LLM Toggle

Three-way toggle: Claude API | Ollama | Off

- **Claude API**: expands to show API key field, model selector
- **Ollama**: expands to show endpoint URL, model name
- **Off**: default, no AI analysis

### Restart Behavior

- Database and Server changes require app restart → yellow banner with "Restart now" link
- Browser and LLM changes take effect on next scan/analysis — Flask re-reads settings.json for these values on each request to the settings API. The settings API endpoint updates both the settings.json file and the in-memory `config` object for `RUN_AI_ANALYSIS`, `CLAUDE_API_KEY`, `CLAUDE_MODEL`, `BROWSER_MODE`, and `PLAYWRIGHT_BROWSERS_PATH`. This avoids requiring a restart for LLM/browser changes.
- "Restart now" sends IPC message to Electron, which re-runs the startup sequence

## Changes to Existing Codebase

### Existing Endpoint (no new file needed)

**`/health`** — Already exists in `auto_a11y/web/app.py`. Returns `{"status": "healthy", "database": "connected"|"disconnected"}`. Already exempted from login in `require_login` before_request handler. Electron's startup poller should check for `status === "healthy"`.

### New Files (Python — ~200 lines total)

**`auto_a11y/web/routes/desktop.py`** — Desktop-mode routes:
- `GET /settings/components` → renders settings page template
- `GET /api/settings` → returns current settings.json
- `POST /api/settings` → writes settings.json, updates in-memory config for immediate-effect settings (LLM, browser), returns success
- `POST /api/settings/test-connection` → tests database/server connectivity
- `POST /shutdown` → graceful Flask shutdown (only available when `DESKTOP_MODE=True`, localhost-only). Used by Electron for cross-platform shutdown.

**`auto_a11y/web/templates/settings/components.html`** — Settings panel template

### Modified Files (Python — ~30 lines of changes total)

**`config.py`** — Add new env vars to the Config dataclass:
- `DESKTOP_MODE: bool = False`
- `USER_DATA_DIR: str = ""`
- `SETTINGS_FILE: str = ""`
- **Primary defense:** Electron's process manager injects `RUN_AI_ANALYSIS=False` as an env var when spawning Python unless a Claude API key is configured in settings.json. This happens before Python starts, so `config.py` reads the correct value at import time.
- **Backup defense:** Modify `validate()` so that when `DESKTOP_MODE=True`, it does not raise `ValueError` for missing `CLAUDE_API_KEY` — instead, silently sets `RUN_AI_ANALYSIS = False`. This catches edge cases where the `.env` file sets `RUN_AI_ANALYSIS=True` but no key is present.
- Add `AUTH_ENABLED: bool = True` (default True for web mode, Electron injects `AUTH_ENABLED=False` for desktop single-user mode)

**`run.py`** — Redirect data directories in desktop mode (~10 lines):
- When `DESKTOP_MODE=True`, create screenshots/, reports/, logs/, temp/ under `USER_DATA_DIR` instead of project root
- Add `--desktop` CLI flag as convenience alias for `DESKTOP_MODE=True` (easier for development/debugging)

**`auto_a11y/web/app.py`** — Register new routes:
- Import and register `settings` blueprint (health endpoint already registered)

**`auto_a11y/web/templates/base.html`** — Add Settings nav link:
- One `<li>` addition, conditional on `config.DESKTOP_MODE`

### Unchanged

All JavaScript test scripts, all existing templates (except base.html nav), test runner, script executor, report generator, formatters, browser manager, database layer, fixture test system, touchpoint system, authentication, all existing routes, static assets, scraper/discovery.

### Deferred (Nice-to-Have)

**Ollama LLM provider** — Abstract `claude_analyzer.py` into an LLMProvider interface with ClaudeProvider (existing code extracted) and OllamaProvider (new, text-only fallback). Can ship v1 without Ollama support; Claude API + Off are sufficient initially.

## New Files — Electron Shell (~500-800 lines JS)

**`electron/main.js`** — App entry point:
- Creates BrowserWindow pointing at Flask URL
- Handles app lifecycle events (ready, window-all-closed, before-quit)
- Shows splash screen during startup
- Delegates to process-manager and settings-manager

**`electron/process-manager.js`** — Core orchestration logic:
- `startMongoDB()` — spawns mongod, polls for readiness, handles port conflicts
- `startFlask()` — spawns Python with env vars, polls /health endpoint
- `stopAll()` — graceful ordered shutdown (Flask first, then MongoDB)
- `monitorProcesses()` — watches for unexpected child process exits
- Error recovery: retry logic, user dialogs, log access

**`electron/settings-manager.js`** — Configuration management:
- Reads settings.json from userData/
- Generates env vars for Python process based on settings
- Creates default settings.json on first run
- Validates settings before applying

**`electron/updater.js`** — Auto-update via electron-updater:
- Checks release server for new versions (GitHub Releases or custom URL)
- Downloads update in background
- Prompts user to restart to apply
- For air-gapped: configurable update server URL, or manual installer replacement

**`electron/splash.html`** — Startup screen:
- Progress bar with step labels ("Starting database...", "Starting server...", "Ready")
- Receives progress updates via IPC from main process

**`electron/package.json`** — Dependencies and electron-builder config:
- electron, electron-builder, electron-updater
- Build targets: NSIS (Windows), DMG (macOS), AppImage + deb (Linux)
- File mappings for python/, app/, mongodb/, chromium/ directories

## Build Pipeline

### Build Script (`build/build.sh`)

Parameterized by target platform (linux, darwin, win32):

1. **Download portable Python 3.12** from python-build-standalone releases for target OS/arch
2. **Install pip dependencies** — `python -m pip install -r requirements.txt` into portable Python's site-packages
3. **Download MongoDB Community Server** binary for target OS — extract just mongod
4. **Download Playwright Chromium** — `python -m playwright install chromium` into bundle's chromium/ directory
5. **Copy auto_a11y source** — app directory with all source, templates, static assets, fixtures
6. **Run electron-builder** — package everything into platform installer

### CI/CD (`.github/workflows/build-desktop.yml`)

GitHub Actions workflow with matrix strategy:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
```

Each platform job:
1. Checkout code
2. Setup Node.js
3. Run build.sh for target platform
4. Run electron-builder
5. Upload installer as workflow artifact
6. On tagged release: upload to GitHub Releases

### Build Output

| Platform | Format | Estimated Size |
|----------|--------|----------------|
| Windows | NSIS .exe installer | ~1.2 GB |
| macOS | .dmg | ~1.1 GB |
| Linux | .AppImage + .deb | ~1.3 GB |

### Auto-Update Configuration

- **Connected environments**: electron-updater checks GitHub Releases for new versions
- **Air-gapped environments**: configurable `updates.server_url` in settings.json pointing to internal HTTP server hosting releases, OR manual installer replacement (app bundle updates, user data preserved)

## Compatibility

### Existing Deployment Unaffected

The desktop distribution is purely additive:
- `python run.py` continues to work as a standalone Flask app
- Cloud deployment (Render) is unaffected
- Docker Compose (MongoDB only) is unaffected
- Desktop mode is opt-in via `DESKTOP_MODE=True` env var

### Data Portability

- MongoDB data format is identical between internal and external modes
- A desktop user can switch to an external MongoDB by providing the URI
- `mongodump`/`mongorestore` can migrate data between installations
- Reports generated in desktop mode are identical to web mode

## First Run Experience

On first launch:
1. No settings.json exists → Electron's settings-manager creates default settings.json with all components set to internal/off
2. MongoDB starts with empty data directory — no collections, no users
3. Flask starts with `DESKTOP_MODE=True` and `AUTH_ENABLED=False` (both injected by Electron) — the `require_login` before_request handler is bypassed when `config.AUTH_ENABLED=False`. The user sees the main dashboard immediately with no login.
4. No projects exist — the existing Flask UI already handles this with "Create your first project" prompts
5. AI analysis is off — the settings panel shows how to enable it with a Claude API key

No setup wizard is needed. The app is immediately usable for non-AI scanning.

## Scheduler Behavior in Desktop Mode

The existing APScheduler integration (`SCHEDULER_ENABLED` in config.py) schedules recurring accessibility tests. In desktop mode:
- **Scheduler runs only while the app is open.** Since the server starts/stops with the app window, scheduled tests will not fire when the app is closed.
- This is a known and documented limitation. The settings panel should display a note when scheduled tests are configured: "Scheduled tests only run while Auto A11y is open."
- For users who need reliable scheduling, the recommendation is to use an external server (Server mode = External) where the auto_a11y instance runs continuously.

## Configuration Precedence

When settings overlap between multiple sources, the precedence order is:

1. **Electron-injected env vars** (from settings.json) — highest priority
2. **userData/.env file** — user overrides for power users
3. **config.py defaults** — lowest priority

Electron reads settings.json first, converts relevant settings to env vars, then spawns Python. Python's `config.py` reads `.env` via `python-dotenv`, then env vars override. Since Electron sets env vars directly on the child process, they take precedence over `.env` file values.

## Platform-Specific Considerations

### macOS Code Signing and Notarization

macOS Gatekeeper blocks unsigned/unnotarized DMGs with increasingly aggressive warnings. For non-technical users, this is effectively a blocker.

**Requirements:**
- Apple Developer Program enrollment ($99/year)
- Code signing certificate for the Electron app
- Notarization via `xcrun notarytool` in the CI/CD pipeline
- electron-builder supports this via `afterSign` hook

**Interim workaround:** If signing is not immediately available, document the "right-click → Open → Open" bypass for users. However, this should be treated as temporary — notarization is required for a professional distribution.

### Windows Code Signing

Optional but recommended. Without signing, Windows SmartScreen shows "Unknown publisher" warnings. An EV code signing certificate eliminates this after building reputation.

### WeasyPrint Native Dependencies

WeasyPrint requires Cairo, Pango, and GdkPixbuf — native C libraries not included in python-build-standalone.

**Per-platform handling in the build script:**
- **Linux**: Bundle the `.so` files from the build system (or use `auditwheel`-style relocation). Most dependencies are available as manylinux wheels.
- **macOS**: Bundle `.dylib` files from Homebrew, fix install names with `install_name_tool`. The build script must install Homebrew deps and relocate them into the bundle.
- **Windows**: GTK3 runtime DLLs (from MSYS2) must be bundled alongside Python. WeasyPrint's Windows wheels on PyPI already include some of these, but the build script should verify completeness.

**Fallback**: If WeasyPrint bundling proves too complex for a platform, PDF generation can be disabled with a graceful message ("PDF export requires additional libraries"). HTML, Excel, JSON, and CSV export still work without WeasyPrint.

### Chromium Double-Bundling

Electron itself bundles Chromium (~120-150MB), and Playwright's Chromium is a separate binary (~250MB). This means ~400MB of Chromium in the installer.

**Why two copies are necessary:** Electron's Chromium runs the UI (BrowserWindow), while Playwright's Chromium runs headless for accessibility testing. They may be different versions with different capabilities (Playwright's headless shell variant is optimized for automation). Sharing Electron's Chromium with Playwright via `executablePath` is fragile — version mismatches cause silent test failures.

**Accepted trade-off:** The ~400MB overhead is acceptable given the "size doesn't matter, prioritize zero-setup" requirement. This is documented so future optimizers understand why.

## Operational Notes

### Minimum Disk Space

- Installation: ~1.5 GB
- MongoDB data: varies (tens of GBs for enterprise-scale)
- Screenshots and reports: varies (~100MB-1GB typical)
- **Recommended minimum: 5 GB free disk space** (displayed in installer requirements)

### Log Rotation

- **mongod**: started with `--logRotate reopen --logappend`, rotated by size via cron or manual `db.adminCommand({logRotate: 1})`
- **Flask**: existing stdlib `logging` with `RotatingFileHandler` in `logging_config.py` (already configured: 10MB, 5 backup files). In desktop mode, only the log file *path* needs to be redirected to `USER_DATA_DIR/logs/`. The existing rotation config is already adequate.
- **Electron**: electron-log with file rotation (default 5 files, 1MB each)

### Data Preservation

- **Updates**: auto-update replaces the app bundle only. User data directory is untouched. Data persists across updates.
- **Uninstall**: platform installers (NSIS, DMG, deb) do NOT delete user data directory by default. Users must manually delete `~/.config/auto-a11y/` (or equivalent) to fully remove data.
- **Backup**: the app should provide an "Export Data" option in settings that runs `mongodump` to a user-chosen directory. This is a nice-to-have for Sub-project 4.

### MongoDB Version and Licensing

- **Version**: MongoDB Community Server 7.0.x (latest stable LTS)
- **License**: SSPL (Server Side Public License). Bundling for internal tooling distribution is permitted — SSPL restrictions apply to offering MongoDB as a service to third parties, which this is not.

## Security Considerations

- **API keys**: stored in settings.json in user data directory (OS-level file permissions)
- **MongoDB**: internal mode binds to localhost only (no network exposure)
- **Flask**: internal mode binds to localhost only
- **No secrets in app bundle**: all credentials in user data directory
- **Auto-update**: electron-updater supports code signing verification
- **Single-user default**: no authentication overhead for personal installations

## Scope Decomposition

This project decomposes into 4 sub-projects, built in order:

### Sub-project 1: Electron Shell + Process Manager
- Electron main process, BrowserWindow, splash screen
- Process manager (spawn/monitor/kill mongod + Python) with cross-platform shutdown
- Settings manager (read/write settings.json)
- Desktop-mode routes in Flask (shutdown endpoint, health endpoint already exists)
- config.py validation fix (graceful handling when no API key)
- DESKTOP_MODE + USER_DATA_DIR env vars, data directory redirection in run.py
- Goal: app launches and shows existing Flask UI

### Sub-project 2: Settings Panel + Component Toggles
- Settings route + template in Flask
- Settings API endpoints
- Internal/external toggle logic for all 4 components
- Desktop mode env vars in config.py
- Goal: users can switch between internal and external components

### Sub-project 3: Build Pipeline + Installers
- Build script for all 3 platforms (including WeasyPrint native deps)
- electron-builder configuration
- GitHub Actions CI/CD workflow
- macOS code signing and notarization
- Windows code signing (if certificate available)
- Goal: produces installable packages for all platforms

### Sub-project 4: Auto-Update + Polish
- electron-updater integration
- Update server configuration (GitHub Releases + air-gapped option)
- Error recovery polish
- Log rotation configuration
- Data export/backup feature (mongodump wrapper)
- Scheduler limitation documentation
- Ollama LLM provider (nice-to-have)
- Goal: production-ready distribution
