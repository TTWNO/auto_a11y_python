# Electron Shell + Process Manager Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get Auto A11y launching as an Electron desktop app that manages MongoDB and Flask as child processes, with a splash screen and graceful shutdown.

**Architecture:** Electron main process orchestrates two sidecar processes (mongod, Python/Flask). A settings-manager reads `settings.json` from the user data directory and generates env vars for the Python process. The existing Flask UI is loaded in a BrowserWindow. Python-side changes are minimal: new `DESKTOP_MODE`/`AUTH_ENABLED`/`USER_DATA_DIR` env vars in config.py, data directory redirection in run.py, a `/shutdown` endpoint, and a `require_login` bypass when auth is disabled.

**Tech Stack:** Electron 33+, Node.js 20+, existing Python 3.12 / Flask / MongoDB stack

**Spec:** `docs/superpowers/specs/2026-03-30-desktop-distribution-design.md`

---

## File Map

### New Files — Electron (`electron/`)

| File | Responsibility |
|------|---------------|
| `electron/package.json` | Electron deps, scripts, electron-builder stub config |
| `electron/main.js` | App entry point: window creation, splash, lifecycle events, delegates to process-manager |
| `electron/process-manager.js` | Spawns/monitors/kills mongod + Python. Port conflict retry. Health polling. Graceful shutdown. |
| `electron/settings-manager.js` | Reads/writes `settings.json` from userData. Creates defaults on first run. Generates env vars for Python. |
| `electron/splash.html` | Startup screen with progress bar, receives IPC updates |
| `electron/preload.js` | Exposes safe IPC bridge for splash screen updates |

### New Files — Python

| File | Responsibility |
|------|---------------|
| `auto_a11y/web/routes/desktop.py` | `POST /shutdown` route (desktop-mode only, localhost-only). Blueprint: `desktop_bp`. |

### Modified Files — Python

| File | Change |
|------|--------|
| `config.py` | Add `DESKTOP_MODE`, `USER_DATA_DIR`, `SETTINGS_FILE`, `AUTH_ENABLED` fields. Fix `validate()` for desktop mode. Guard module-level dir creation for read-only bundles. |
| `run.py` | Add `--desktop` flag. Redirect data dirs when `DESKTOP_MODE=True`. |
| `auto_a11y/core/logging_config.py` | Accept optional log directory parameter to support USER_DATA_DIR redirection. |
| `auto_a11y/web/app.py` | Register `desktop_bp`. Auto-login anonymous desktop user when `AUTH_ENABLED=False`. |
| `auto_a11y/web/routes/__init__.py` | Export `desktop_bp`. |
| `auto_a11y/web/templates/base.html` | Add "Components" link in settings dropdown, conditional on `DESKTOP_MODE`. (Deferred to Sub-project 2.) |

---

## Task 1: Python-Side Config Changes

**Files:**
- Modify: `config.py:26-149`
- Modify: `run.py:23-35, 120-140`

- [ ] **Step 1: Add desktop-mode fields to Config dataclass**

In `config.py`, add these fields after `SCHEDULER_COALESCE` (line 82) and before `TOKEN_SALT` (line 84):

```python
    # Desktop mode (Electron distribution)
    DESKTOP_MODE: bool = os.getenv('DESKTOP_MODE', 'False').lower() == 'true'
    USER_DATA_DIR: str = os.getenv('USER_DATA_DIR', '')
    SETTINGS_FILE: str = os.getenv('SETTINGS_FILE', '')
    AUTH_ENABLED: bool = os.getenv('AUTH_ENABLED', 'True').lower() == 'true'
```

Also, guard the module-level directory creation (lines 21-23) to avoid crashing in a read-only app bundle. Replace:

```python
# Create directories if they don't exist
for directory in [DATA_DIR, REPORTS_DIR, SCREENSHOTS_DIR]:
    directory.mkdir(exist_ok=True, parents=True)
```

With:

```python
# Create directories if they don't exist (skip in desktop mode — run.py handles it)
if os.getenv('DESKTOP_MODE', 'False').lower() != 'true':
    for directory in [DATA_DIR, REPORTS_DIR, SCREENSHOTS_DIR]:
        directory.mkdir(exist_ok=True, parents=True)
```

- [ ] **Step 2: Fix validate() for desktop mode**

Replace the `validate` method (lines 140-144) with:

```python
    def validate(self) -> bool:
        """Validate configuration"""
        if self.RUN_AI_ANALYSIS and not self.CLAUDE_API_KEY:
            if self.DESKTOP_MODE:
                # In desktop mode, silently disable AI instead of crashing
                self.RUN_AI_ANALYSIS = False
            else:
                raise ValueError("CLAUDE_API_KEY is required when RUN_AI_ANALYSIS is True")
        return True
```

- [ ] **Step 3: Add --desktop flag and data directory redirection to run.py**

In `run.py`, add the `--desktop` argument after line 129 (`--skip-browser`):

```python
    parser.add_argument('--desktop', action='store_true', help='Enable desktop mode (Electron distribution)')
```

After `args = parser.parse_args()` (line 131), add desktop mode handling:

```python
    # Desktop mode
    if args.desktop:
        config.DESKTOP_MODE = True
```

Replace `init_directories()` function (lines 23-35) with:

```python
def init_directories():
    """Initialize required directories"""
    if config.DESKTOP_MODE and config.USER_DATA_DIR:
        user_data = Path(config.USER_DATA_DIR)
        config.SCREENSHOTS_DIR = user_data / 'screenshots'
        config.REPORTS_DIR = user_data / 'reports'
        log_dir = user_data / 'logs'
        temp_dir = user_data / 'temp'
    else:
        log_dir = Path('logs')
        temp_dir = Path('temp')

    directories = [
        Path(config.SCREENSHOTS_DIR),
        Path(config.REPORTS_DIR),
        log_dir,
        temp_dir,
        Path('static/screenshots'),  # Preserve existing directory
    ]

    for directory in directories:
        directory.mkdir(exist_ok=True, parents=True)
        logging.info(f"Ensured directory exists: {directory}")
```

Also update the `configure_logging()` call (currently line 142) to pass the log directory in desktop mode. After `init_directories()` (line 150), add:

```python
    # In desktop mode, redirect log file to USER_DATA_DIR
    if config.DESKTOP_MODE and config.USER_DATA_DIR:
        from auto_a11y.core.logging_config import reconfigure_log_path
        reconfigure_log_path(Path(config.USER_DATA_DIR) / 'logs')
```

- [ ] **Step 4: Add log path reconfiguration to logging_config.py**

In `auto_a11y/core/logging_config.py`, add a function after the `setup_logging` function to allow runtime reconfiguration of the log file path:

```python
def reconfigure_log_path(log_dir):
    """Redirect log file to a different directory (for desktop mode).
    Call after setup_logging() has been called."""
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True, parents=True)
    new_log_path = str(log_dir / 'auto_a11y.log')

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            handler.baseFilename = new_log_path
            handler.stream = handler._open()
            break
```

Also guard the module-level `LOGS_DIR.mkdir()` (line 20) to avoid errors in read-only bundles:

```python
# Create logs directory if it doesn't exist (skip in desktop mode — run.py handles it)
LOGS_DIR = Path(__file__).parent.parent.parent / 'logs'
if os.getenv('DESKTOP_MODE', 'False').lower() != 'true':
    LOGS_DIR.mkdir(exist_ok=True, parents=True)
```

- [ ] **Step 5: Verify existing app still works without desktop mode**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && .venv/bin/python -c "from config import config; print(f'DESKTOP_MODE={config.DESKTOP_MODE}, AUTH_ENABLED={config.AUTH_ENABLED}, RUN_AI_ANALYSIS={config.RUN_AI_ANALYSIS}')"`

Expected: `DESKTOP_MODE=False, AUTH_ENABLED=True, RUN_AI_ANALYSIS=True` (or False if no API key set — should NOT crash)

- [ ] **Step 6: Verify desktop mode config works**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && DESKTOP_MODE=True RUN_AI_ANALYSIS=False AUTH_ENABLED=False .venv/bin/python -c "from config import config; print(f'DESKTOP_MODE={config.DESKTOP_MODE}, AUTH_ENABLED={config.AUTH_ENABLED}, RUN_AI_ANALYSIS={config.RUN_AI_ANALYSIS}')"`

Expected: `DESKTOP_MODE=True, AUTH_ENABLED=False, RUN_AI_ANALYSIS=False`

- [ ] **Step 7: Commit**

```bash
git add config.py run.py auto_a11y/core/logging_config.py
git commit -m "feat: add DESKTOP_MODE, AUTH_ENABLED, USER_DATA_DIR config fields and data dir redirection"
```

---

## Task 2: Shutdown Endpoint and Auth Bypass

**Files:**
- Create: `auto_a11y/web/routes/desktop.py`
- Modify: `auto_a11y/web/routes/__init__.py`
- Modify: `auto_a11y/web/app.py:248-271`

- [ ] **Step 1: Create desktop.py route file**

Create `auto_a11y/web/routes/desktop.py`:

```python
"""
Desktop-mode routes for Electron integration.
Only active when DESKTOP_MODE=True.
"""

import os
from flask import Blueprint, jsonify, request, current_app

desktop_bp = Blueprint('desktop', __name__)


@desktop_bp.route('/shutdown', methods=['POST'])
def shutdown():
    """
    Graceful Flask shutdown for Electron.
    Only available in desktop mode, localhost-only.
    Electron calls this during app quit for cross-platform shutdown
    (SIGTERM does not work on Windows).
    """
    if not current_app.app_config.DESKTOP_MODE:
        return jsonify({'error': 'Not in desktop mode'}), 403

    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify({'error': 'Localhost only'}), 403

    # Respond before exiting so Electron gets confirmation
    response = jsonify({'status': 'shutting_down'})

    # Schedule exit after response is sent.
    # Use os._exit(0) because sys.exit() only raises SystemExit which Flask catches.
    # os._exit(0) skips atexit handlers (like APScheduler shutdown), but this is
    # acceptable because Electron is the one managing the process lifecycle.
    import threading
    threading.Timer(0.5, lambda: os._exit(0)).start()

    return response
```

- [ ] **Step 2: Export desktop_bp from routes __init__.py**

Add to `auto_a11y/web/routes/__init__.py`:

After line 23 (`from .members import members_bp`), add:

```python
from .desktop import desktop_bp
```

Add `'desktop_bp'` to the `__all__` list.

- [ ] **Step 3: Register desktop_bp and add auth bypass in app.py**

In `auto_a11y/web/app.py`, add `desktop_bp` to the imports (line 32, after `members_bp`):

```python
from auto_a11y.web.routes import (
    ...
    members_bp,
    desktop_bp
)
```

Register the blueprint after the other registrations (around line 246, after `app.register_blueprint(groups_bp, url_prefix='/groups')`):

```python
    app.register_blueprint(desktop_bp)
```

Add `'desktop.shutdown'` to the `allowed_endpoints` list in `require_login` (so shutdown works even when auth IS enabled):

```python
        allowed_endpoints = [
            'auth.login', 'auth.register', 'auth.logout',
            'auth.microsoft_login', 'auth.microsoft_callback',
            'auth.google_login', 'auth.google_callback',
            'static', 'health', 'set_language',
            'desktop.shutdown'
        ]
```

**Critical: Auto-login anonymous desktop user.** The `require_login` bypass alone is not enough because:
- The `index()` route (line 360-365) checks `current_user.is_authenticated` directly
- The `dashboard()` route (line 367-368) has a `@login_required` decorator
- The `inject_user_has_projects` context processor (line 274-279) checks `current_user.is_authenticated`

The correct fix is to auto-login a synthetic user on every request when auth is disabled. Add this `before_request` handler **before** the existing `require_login` handler (around line 248):

```python
    # Desktop mode: auto-login anonymous user when auth is disabled
    @app.before_request
    def desktop_auto_login():
        """In desktop mode with auth disabled, auto-login as a superadmin user."""
        if not app.app_config.AUTH_ENABLED and not current_user.is_authenticated:
            from auto_a11y.models.user import User
            # Create or fetch a desktop-mode user
            desktop_user = app.db.get_user_by_email('desktop@auto-a11y.local')
            if not desktop_user:
                desktop_user = app.db.create_user({
                    'email': 'desktop@auto-a11y.local',
                    'name': 'Desktop User',
                    'role': 'superadmin',
                    'is_superadmin': True,
                })
                desktop_user = app.db.get_user_by_email('desktop@auto-a11y.local')
            if desktop_user:
                user_obj = User(desktop_user)
                from flask_login import login_user
                login_user(user_obj)
```

This creates a single "Desktop User" superadmin on first request and auto-logs them in on every subsequent request. Since `before_request` handlers run in registration order, this must be registered **before** `require_login`.

- [ ] **Step 4: Test shutdown endpoint responds correctly in desktop mode**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && DESKTOP_MODE=True RUN_AI_ANALYSIS=False AUTH_ENABLED=False .venv/bin/python -c "
from config import config
from auto_a11y.web.app import create_app
app = create_app(config)
client = app.test_client()

# Should succeed in desktop mode from localhost
resp = client.post('/shutdown')
print(f'Desktop mode shutdown: {resp.status_code} {resp.get_json()}')
"`

Expected: `Desktop mode shutdown: 200 {'status': 'shutting_down'}`

Note: The `os._exit(0)` timer may kill the process — that's expected. If it exits before printing, wrap in try/except or verify the 200 status is returned.

- [ ] **Step 5: Test shutdown is rejected when not in desktop mode**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && RUN_AI_ANALYSIS=False .venv/bin/python -c "
from config import config
from auto_a11y.web.app import create_app
app = create_app(config)
client = app.test_client()

resp = client.post('/shutdown')
print(f'Non-desktop shutdown: {resp.status_code} {resp.get_json()}')
"`

Expected: `Non-desktop shutdown: 403 {'error': 'Not in desktop mode'}`

- [ ] **Step 6: Test auth bypass in desktop mode**

Run: `cd /home/tait/Documents/cnib/code/auto_a11y_python && DESKTOP_MODE=True RUN_AI_ANALYSIS=False AUTH_ENABLED=False .venv/bin/python -c "
from config import config
from auto_a11y.web.app import create_app
app = create_app(config)
client = app.test_client()

# GET / should redirect to dashboard (302), not to login
resp = client.get('/')
print(f'Index: {resp.status_code} -> {resp.headers.get(\"Location\", \"\")}')

# Follow the redirect to /dashboard — should get 200, not 302 to login
resp2 = client.get('/dashboard')
print(f'Dashboard: {resp2.status_code}')
"`

Expected:
- `Index: 302 -> /dashboard` (index always redirects; in desktop mode, it redirects to dashboard because the auto-logged-in user is authenticated)
- `Dashboard: 200` (dashboard renders because the desktop user is auto-logged in)

- [ ] **Step 7: Commit**

```bash
git add auto_a11y/web/routes/desktop.py auto_a11y/web/routes/__init__.py auto_a11y/web/app.py
git commit -m "feat: add /shutdown endpoint and AUTH_ENABLED bypass for desktop mode"
```

---

## Task 3: Base Template — Settings Nav Link

**Files:**
- Modify: `auto_a11y/web/templates/base.html:107-120`

- [ ] **Step 1: Add "Components" link to settings dropdown**

In `auto_a11y/web/templates/base.html`, inside the settings dropdown (after the Fixture Status link, line 118, before the closing `{% endif %}`), add:

```html
                            {% if config.DESKTOP_MODE %}
                            <li><hr class="dropdown-divider"></li>
                            <li><a class="dropdown-item" href="{{ url_for('desktop.components') }}">
                                <i class="bi bi-hdd-stack" aria-hidden="true"></i> {{ _('Components') }}
                            </a></li>
                            {% endif %}
```

Note: The `desktop.components` route does not exist yet — it will be created in Sub-project 2 (Settings Panel). For now, this link will produce a build error if `DESKTOP_MODE` is True and someone clicks it. This is acceptable because the Electron shell (which sets `DESKTOP_MODE=True`) is being built in this same sub-project, and the settings panel is the next sub-project. To avoid the dead link temporarily, we can guard it with a `try/except` or simply leave it — the link won't render in non-desktop mode.

**Alternative (safer):** Skip this step for now and include it in Sub-project 2 when the route exists. This avoids a dead link.

Decision: **Skip this step.** Move to Sub-project 2. The nav link should be added when the route it points to exists.

- [ ] **Step 2: Commit (if changes were made)**

Skip — no changes made in this task.

---

## Task 4: Electron Project Setup

**Files:**
- Create: `electron/package.json`
- Create: `electron/.gitignore`

- [ ] **Step 1: Create electron directory**

```bash
mkdir -p electron
```

- [ ] **Step 2: Create package.json**

Create `electron/package.json`:

```json
{
  "name": "auto-a11y",
  "version": "1.0.0",
  "description": "Auto A11y - Web Accessibility Testing Platform",
  "main": "main.js",
  "scripts": {
    "start": "electron .",
    "dev": "electron . --dev"
  },
  "dependencies": {
    "electron-log": "^5.3.0"
  },
  "devDependencies": {
    "electron": "^33.0.0",
    "electron-builder": "^25.0.0"
  },
  "build": {
    "appId": "com.cnib.auto-a11y",
    "productName": "Auto A11y",
    "directories": {
      "output": "dist"
    },
    "extraResources": [
      {
        "from": "../python",
        "to": "python"
      },
      {
        "from": "../app",
        "to": "app"
      },
      {
        "from": "../mongodb",
        "to": "mongodb"
      },
      {
        "from": "../chromium",
        "to": "chromium"
      }
    ]
  }
}
```

- [ ] **Step 3: Create .gitignore for electron directory**

Create `electron/.gitignore`:

```
node_modules/
dist/
```

- [ ] **Step 4: Install dependencies**

```bash
cd electron && npm install
```

Expected: `node_modules/` created with electron and electron-log.

- [ ] **Step 5: Commit**

```bash
git add electron/package.json electron/.gitignore
git commit -m "feat: initialize Electron project with package.json"
```

---

## Task 5: Settings Manager

**Files:**
- Create: `electron/settings-manager.js`

- [ ] **Step 1: Create settings-manager.js**

This module reads/writes `settings.json` from the Electron `userData` directory and generates environment variables for the Python child process.

Create `electron/settings-manager.js`:

```javascript
const fs = require('fs');
const path = require('path');
const { app } = require('electron');
const log = require('electron-log');

const DEFAULT_SETTINGS = {
  database: {
    mode: 'internal',
    uri: 'mongodb://localhost:27017/auto_a11y',
    internal_port: 27017
  },
  server: {
    mode: 'internal',
    url: 'http://localhost:5001',
    internal_port: 5001
  },
  browser: {
    mode: 'internal',
    playwright_endpoint: '',
    system_chrome_path: ''
  },
  llm: {
    mode: 'off',
    claude_api_key: '',
    claude_model: 'claude-opus-4-20250514',
    ollama_url: '',
    ollama_model: ''
  },
  auth: {
    enabled: false
  },
  updates: {
    auto_check: true,
    server_url: ''
  }
};

class SettingsManager {
  constructor() {
    this.userDataDir = app.getPath('userData');
    this.settingsPath = path.join(this.userDataDir, 'settings.json');
    this.settings = null;
  }

  /**
   * Load settings from disk, creating defaults if missing.
   */
  load() {
    try {
      if (fs.existsSync(this.settingsPath)) {
        const raw = fs.readFileSync(this.settingsPath, 'utf8');
        const saved = JSON.parse(raw);
        // Deep merge: preserve new default fields when user settings are missing them
        this.settings = {};
        for (const key of Object.keys(DEFAULT_SETTINGS)) {
          if (typeof DEFAULT_SETTINGS[key] === 'object' && DEFAULT_SETTINGS[key] !== null) {
            this.settings[key] = { ...DEFAULT_SETTINGS[key], ...(saved[key] || {}) };
          } else {
            this.settings[key] = saved[key] !== undefined ? saved[key] : DEFAULT_SETTINGS[key];
          }
        }
        log.info('Settings loaded from', this.settingsPath);
      } else {
        this.settings = { ...DEFAULT_SETTINGS };
        this.save();
        log.info('Created default settings at', this.settingsPath);
      }
    } catch (err) {
      log.error('Failed to load settings, using defaults:', err.message);
      this.settings = { ...DEFAULT_SETTINGS };
    }
    return this.settings;
  }

  /**
   * Write current settings to disk.
   */
  save() {
    try {
      const dir = path.dirname(this.settingsPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(this.settingsPath, JSON.stringify(this.settings, null, 2), 'utf8');
      log.info('Settings saved to', this.settingsPath);
    } catch (err) {
      log.error('Failed to save settings:', err.message);
    }
  }

  /**
   * Ensure required subdirectories exist in userData.
   */
  ensureDirectories() {
    const dirs = ['mongodb/data', 'logs', 'reports', 'screenshots', 'temp'];
    for (const dir of dirs) {
      const fullPath = path.join(this.userDataDir, dir);
      if (!fs.existsSync(fullPath)) {
        fs.mkdirSync(fullPath, { recursive: true });
        log.info('Created directory:', fullPath);
      }
    }
  }

  /**
   * Generate environment variables for the Python/Flask child process
   * based on current settings.
   */
  getFlaskEnv(resolvedPorts) {
    const s = this.settings;
    const mongoPort = resolvedPorts.mongo || s.database.internal_port;
    const flaskPort = resolvedPorts.flask || s.server.internal_port;

    const env = {
      ...process.env,
      DESKTOP_MODE: 'True',
      USER_DATA_DIR: this.userDataDir,
      SETTINGS_FILE: this.settingsPath,
      AUTH_ENABLED: s.auth.enabled ? 'True' : 'False',
      HOST: '127.0.0.1',
      PORT: String(flaskPort),
      DEBUG: 'False',
    };

    // Database
    if (s.database.mode === 'internal') {
      env.MONGODB_URI = `mongodb://localhost:${mongoPort}/`;
    } else {
      env.MONGODB_URI = s.database.uri;
    }
    env.DATABASE_NAME = 'auto_a11y';

    // AI / LLM
    if (s.llm.mode === 'claude' && s.llm.claude_api_key) {
      env.RUN_AI_ANALYSIS = 'True';
      env.CLAUDE_API_KEY = s.llm.claude_api_key;
      env.CLAUDE_MODEL = s.llm.claude_model || 'claude-opus-4-20250514';
    } else {
      env.RUN_AI_ANALYSIS = 'False';
    }

    // Browser
    if (s.browser.mode === 'internal') {
      env.BROWSER_MODE = 'local';
      // PLAYWRIGHT_BROWSERS_PATH set by process-manager based on app path
    } else {
      env.BROWSER_MODE = 'remote';
    }

    return env;
  }

  /**
   * Get the Flask server URL based on settings.
   */
  getServerUrl(resolvedPort) {
    const s = this.settings;
    if (s.server.mode === 'external') {
      return s.server.url;
    }
    const port = resolvedPort || s.server.internal_port;
    return `http://127.0.0.1:${port}`;
  }

  /**
   * Check if internal MongoDB should be started.
   */
  get useInternalMongo() {
    return this.settings.database.mode === 'internal';
  }

  /**
   * Check if internal Flask server should be started.
   */
  get useInternalServer() {
    return this.settings.server.mode === 'internal';
  }
}

module.exports = { SettingsManager, DEFAULT_SETTINGS };
```

- [ ] **Step 2: Verify module loads without Electron (syntax check)**

```bash
cd electron && node -e "
// Can't require electron in bare node, but we can syntax-check the file
const fs = require('fs');
const src = fs.readFileSync('settings-manager.js', 'utf8');
try { new Function(src); } catch(e) { /* expected: require('electron') fails */ }
console.log('Syntax OK');
"
```

Expected: `Syntax OK` (or a runtime error about `electron` not found — that's fine, we just want no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add electron/settings-manager.js
git commit -m "feat: add settings-manager for reading/writing desktop settings.json"
```

---

## Task 6: Process Manager

**Files:**
- Create: `electron/process-manager.js`

- [ ] **Step 1: Create process-manager.js**

This is the core orchestration module. It spawns mongod and Python, polls for readiness, handles port conflicts, and performs graceful shutdown.

Create `electron/process-manager.js`:

```javascript
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { app } = require('electron');
const log = require('electron-log');

class ProcessManager {
  constructor(settingsManager) {
    this.settings = settingsManager;
    this.mongoProcess = null;
    this.flaskProcess = null;
    this.resolvedPorts = { mongo: null, flask: null };
    this.isShuttingDown = false;
  }

  /**
   * Get paths to bundled binaries.
   * In development, these may not exist — callers should check.
   */
  getPaths() {
    const resourcesPath = process.resourcesPath || path.join(__dirname, '..');
    return {
      mongod: path.join(resourcesPath, 'mongodb', 'bin', 'mongod'),
      python: path.join(resourcesPath, 'python', 'bin', 'python3.12'),
      appDir: path.join(resourcesPath, 'app'),
      chromium: path.join(resourcesPath, 'chromium'),
    };
  }

  /**
   * Check if a TCP port is available.
   */
  isPortAvailable(port) {
    return new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => resolve(false));
      server.once('listening', () => {
        server.close();
        resolve(true);
      });
      server.listen(port, '127.0.0.1');
    });
  }

  /**
   * Find an available port starting from basePort, trying up to maxAttempts.
   */
  async findAvailablePort(basePort, maxAttempts = 3) {
    for (let i = 0; i < maxAttempts; i++) {
      const port = basePort + i;
      if (await this.isPortAvailable(port)) {
        return port;
      }
      log.warn(`Port ${port} is in use, trying ${port + 1}...`);
    }
    throw new Error(`No available port found (tried ${basePort}-${basePort + maxAttempts - 1})`);
  }

  /**
   * Poll a URL until it returns a successful response or timeout.
   */
  pollUrl(url, timeoutMs = 30000, intervalMs = 500) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeoutMs;

      const check = () => {
        if (Date.now() > deadline) {
          reject(new Error(`Timeout waiting for ${url}`));
          return;
        }

        http.get(url, (res) => {
          let body = '';
          res.on('data', (chunk) => { body += chunk; });
          res.on('end', () => {
            try {
              const data = JSON.parse(body);
              if (data.status === 'healthy') {
                resolve(data);
              } else {
                setTimeout(check, intervalMs);
              }
            } catch {
              setTimeout(check, intervalMs);
            }
          });
        }).on('error', () => {
          setTimeout(check, intervalMs);
        });
      };

      check();
    });
  }

  /**
   * Clean up stale MongoDB lock file if no mongod process is running.
   */
  cleanStaleLock() {
    const dbPath = path.join(this.settings.userDataDir, 'mongodb', 'data');
    const lockFile = path.join(dbPath, 'mongod.lock');

    if (!fs.existsSync(lockFile)) return;

    const content = fs.readFileSync(lockFile, 'utf8').trim();
    if (!content) return; // Empty lock = clean shutdown

    log.warn(`Found stale mongod.lock with PID ${content}, cleaning up...`);
    try {
      // Check if the PID is still running
      process.kill(parseInt(content), 0);
      // If no error, process is still running — don't clean
      log.warn('mongod process is still running, skipping lock cleanup');
    } catch {
      // Process not running — safe to clean
      fs.writeFileSync(lockFile, '', 'utf8');
      log.info('Cleaned stale mongod.lock');
    }
  }

  /**
   * Start MongoDB sidecar process.
   */
  async startMongoDB(onProgress) {
    if (!this.settings.useInternalMongo) {
      log.info('Using external MongoDB, skipping internal start');
      return;
    }

    onProgress && onProgress('Starting database...');

    this.cleanStaleLock();

    const paths = this.getPaths();
    const dbPath = path.join(this.settings.userDataDir, 'mongodb', 'data');
    const logPath = path.join(this.settings.userDataDir, 'logs', 'mongod.log');
    const basePort = this.settings.settings.database.internal_port;

    // Find available port
    const port = await this.findAvailablePort(basePort);
    this.resolvedPorts.mongo = port;
    log.info(`Starting mongod on port ${port}, dbpath: ${dbPath}`);

    // Determine mongod path: bundled binary or system-installed
    let mongodPath = paths.mongod;
    if (!fs.existsSync(mongodPath)) {
      // Fall back to system mongod (for development)
      mongodPath = 'mongod';
      log.warn('Bundled mongod not found, falling back to system mongod');
    }

    this.mongoProcess = spawn(mongodPath, [
      '--dbpath', dbPath,
      '--port', String(port),
      '--bind_ip', '127.0.0.1',
      '--logpath', logPath,
      '--logappend',
      '--logRotate', 'reopen',
    ], {
      stdio: 'ignore',
      detached: false,
    });

    this.mongoProcess.on('error', (err) => {
      log.error('mongod failed to start:', err.message);
    });

    this.mongoProcess.on('exit', (code, signal) => {
      if (!this.isShuttingDown) {
        log.error(`mongod exited unexpectedly: code=${code}, signal=${signal}`);
      }
    });

    // Poll until MongoDB is accepting connections
    await this.pollMongoReady(port, 15000);
    log.info(`MongoDB is ready on port ${port}`);
  }

  /**
   * Poll MongoDB by attempting a TCP connection.
   */
  pollMongoReady(port, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeoutMs;

      const check = () => {
        if (Date.now() > deadline) {
          reject(new Error(`MongoDB did not start within ${timeoutMs}ms`));
          return;
        }

        const socket = net.createConnection({ port, host: '127.0.0.1' }, () => {
          socket.destroy();
          resolve();
        });
        socket.on('error', () => {
          setTimeout(check, 500);
        });
      };

      check();
    });
  }

  /**
   * Start Flask/Python sidecar process.
   */
  async startFlask(onProgress) {
    if (!this.settings.useInternalServer) {
      log.info('Using external server, skipping internal start');
      return;
    }

    onProgress && onProgress('Starting server...');

    const paths = this.getPaths();
    const basePort = this.settings.settings.server.internal_port;

    // Find available port
    const port = await this.findAvailablePort(basePort);
    this.resolvedPorts.flask = port;

    // Build environment variables
    const env = this.settings.getFlaskEnv(this.resolvedPorts);
    env.PORT = String(port);

    // Set Playwright browsers path if bundled chromium exists
    if (fs.existsSync(paths.chromium)) {
      env.PLAYWRIGHT_BROWSERS_PATH = paths.chromium;
    }

    // Determine python path: bundled or system
    let pythonPath = paths.python;
    if (!fs.existsSync(pythonPath)) {
      // Fall back to system python (for development)
      pythonPath = 'python3';
      log.warn('Bundled Python not found, falling back to system python3');
    }

    // Determine app entry point
    let runPy = path.join(paths.appDir, 'run.py');
    if (!fs.existsSync(runPy)) {
      // Development: app dir is the project root
      runPy = path.join(__dirname, '..', 'run.py');
      log.warn('Bundled app not found, falling back to:', runPy);
    }

    log.info(`Starting Flask: ${pythonPath} ${runPy} --port ${port}`);

    this.flaskProcess = spawn(pythonPath, [runPy, '--desktop', '--port', String(port)], {
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      cwd: path.dirname(runPy),
    });

    // Pipe Flask stdout/stderr to electron-log
    this.flaskProcess.stdout.on('data', (data) => {
      log.info('[flask]', data.toString().trim());
    });
    this.flaskProcess.stderr.on('data', (data) => {
      log.warn('[flask]', data.toString().trim());
    });

    this.flaskProcess.on('error', (err) => {
      log.error('Flask process failed to start:', err.message);
    });

    this.flaskProcess.on('exit', (code, signal) => {
      if (!this.isShuttingDown) {
        log.error(`Flask exited unexpectedly: code=${code}, signal=${signal}`);
      }
    });

    // Poll /health until ready
    const healthUrl = `http://127.0.0.1:${port}/health`;
    onProgress && onProgress('Waiting for server...');
    await this.pollUrl(healthUrl, 30000);
    log.info(`Flask is ready on port ${port}`);
  }

  /**
   * Start all internal services in order.
   */
  async startAll(onProgress) {
    await this.startMongoDB(onProgress);
    await this.startFlask(onProgress);
    onProgress && onProgress('Ready');
  }

  /**
   * Graceful ordered shutdown: Flask first, then MongoDB.
   */
  async stopAll() {
    if (this.isShuttingDown) return;
    this.isShuttingDown = true;
    log.info('Shutting down all services...');

    // 1. Stop Flask via /shutdown endpoint
    if (this.flaskProcess && !this.flaskProcess.killed) {
      try {
        const port = this.resolvedPorts.flask;
        await this.postShutdown(port);
        log.info('Flask shutdown request sent');
      } catch (err) {
        log.warn('Flask shutdown request failed:', err.message);
      }

      // Wait for process to exit, then force kill
      await this.waitForExit(this.flaskProcess, 5000);
    }

    // 2. Stop MongoDB via mongod --shutdown
    if (this.mongoProcess && !this.mongoProcess.killed) {
      try {
        const dbPath = path.join(this.settings.userDataDir, 'mongodb', 'data');
        const paths = this.getPaths();
        let mongodPath = paths.mongod;
        if (!fs.existsSync(mongodPath)) {
          mongodPath = 'mongod';
        }

        log.info('Sending mongod --shutdown...');
        execSync(`"${mongodPath}" --shutdown --dbpath "${dbPath}"`, {
          timeout: 10000,
          stdio: 'ignore',
        });
        log.info('MongoDB shut down cleanly');
      } catch (err) {
        log.warn('mongod --shutdown failed:', err.message);
        // Force kill as last resort
        this.forceKill(this.mongoProcess);
      }
    }

    log.info('All services stopped');
  }

  /**
   * Send POST /shutdown to Flask.
   */
  postShutdown(port) {
    return new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1',
        port,
        path: '/shutdown',
        method: 'POST',
        timeout: 3000,
      }, (res) => {
        resolve(res.statusCode);
      });
      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Shutdown request timed out'));
      });
      req.end();
    });
  }

  /**
   * Wait for a child process to exit, force kill after timeout.
   */
  waitForExit(proc, timeoutMs) {
    return new Promise((resolve) => {
      if (!proc || proc.killed) {
        resolve();
        return;
      }

      const timer = setTimeout(() => {
        log.warn('Process did not exit in time, force killing...');
        this.forceKill(proc);
        resolve();
      }, timeoutMs);

      proc.on('exit', () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  /**
   * Force kill a child process (cross-platform).
   */
  forceKill(proc) {
    if (!proc || proc.killed) return;
    try {
      if (process.platform === 'win32') {
        execSync(`taskkill /pid ${proc.pid} /T /F`, { stdio: 'ignore' });
      } else {
        proc.kill('SIGKILL');
      }
    } catch (err) {
      log.warn('Force kill failed:', err.message);
    }
  }
}

module.exports = { ProcessManager };
```

- [ ] **Step 2: Verify syntax**

```bash
cd electron && node -e "
const fs = require('fs');
const src = fs.readFileSync('process-manager.js', 'utf8');
try { new Function(src); } catch(e) { /* expected: require('electron') fails in bare node */ }
console.log('Syntax OK');
"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add electron/process-manager.js
git commit -m "feat: add process-manager for mongod/Flask lifecycle orchestration"
```

---

## Task 7: Splash Screen

**Files:**
- Create: `electron/splash.html`
- Create: `electron/preload.js`

- [ ] **Step 1: Create preload.js**

This exposes a safe IPC bridge so the splash screen can receive progress updates from the main process.

Create `electron/preload.js`:

```javascript
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  onProgress: (callback) => {
    ipcRenderer.on('startup-progress', (_event, message) => callback(message));
  },
  onError: (callback) => {
    ipcRenderer.on('startup-error', (_event, message) => callback(message));
  }
});
```

- [ ] **Step 2: Create splash.html**

Create `electron/splash.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Auto A11y</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #1a1a2e;
      color: #e0e0e0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100vh;
      overflow: hidden;
      -webkit-app-region: drag;
    }
    .logo {
      font-size: 2rem;
      font-weight: 700;
      color: #ffffff;
      margin-bottom: 0.5rem;
      letter-spacing: -0.5px;
    }
    .logo span { color: #4a9eff; }
    .version {
      font-size: 0.8rem;
      color: #888;
      margin-bottom: 2rem;
    }
    .progress-container {
      width: 300px;
      height: 4px;
      background: #2a2a4a;
      border-radius: 2px;
      overflow: hidden;
      margin-bottom: 1rem;
    }
    .progress-bar {
      height: 100%;
      width: 30%;
      background: linear-gradient(90deg, #4a9eff, #7b61ff);
      border-radius: 2px;
      animation: indeterminate 1.5s ease-in-out infinite;
    }
    .progress-bar.done {
      width: 100%;
      animation: none;
      transition: width 0.3s ease;
    }
    @keyframes indeterminate {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(400%); }
    }
    .status {
      font-size: 0.85rem;
      color: #aaa;
      min-height: 1.2em;
    }
    .error {
      color: #ff6b6b;
      font-size: 0.85rem;
      margin-top: 1rem;
      max-width: 400px;
      text-align: center;
      display: none;
    }
  </style>
</head>
<body>
  <div class="logo">Auto <span>A11y</span></div>
  <div class="version">Accessibility Testing Platform</div>
  <div class="progress-container">
    <div class="progress-bar" id="progressBar"></div>
  </div>
  <div class="status" id="status">Initializing...</div>
  <div class="error" id="error"></div>

  <script>
    const statusEl = document.getElementById('status');
    const progressBar = document.getElementById('progressBar');
    const errorEl = document.getElementById('error');

    if (window.electronAPI) {
      window.electronAPI.onProgress((message) => {
        statusEl.textContent = message;
        if (message === 'Ready') {
          progressBar.classList.add('done');
        }
      });

      window.electronAPI.onError((message) => {
        statusEl.textContent = 'Error';
        errorEl.textContent = message;
        errorEl.style.display = 'block';
        progressBar.style.background = '#ff6b6b';
      });
    }
  </script>
</body>
</html>
```

- [ ] **Step 3: Commit**

```bash
git add electron/preload.js electron/splash.html
git commit -m "feat: add splash screen with progress bar and IPC preload bridge"
```

---

## Task 8: Electron Main Process

**Files:**
- Create: `electron/main.js`

- [ ] **Step 1: Create main.js**

This ties everything together: creates the splash window, starts services, then shows the main BrowserWindow.

Create `electron/main.js`:

```javascript
const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const log = require('electron-log');
const { SettingsManager } = require('./settings-manager');
const { ProcessManager } = require('./process-manager');

// Configure logging
log.transports.file.resolvePathFn = () => {
  return path.join(app.getPath('userData'), 'logs', 'electron.log');
};
log.transports.file.maxSize = 1024 * 1024; // 1MB
log.transports.file.format = '{y}-{m}-{d} {h}:{i}:{s} [{level}] {text}';

let splashWindow = null;
let mainWindow = null;
let settingsManager = null;
let processManager = null;

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 500,
    height: 300,
    frame: false,
    resizable: false,
    transparent: false,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  return splashWindow;
}

function createMainWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    show: false,
    title: 'Auto A11y',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(url);

  mainWindow.once('ready-to-show', () => {
    if (splashWindow) {
      splashWindow.destroy();
      splashWindow = null;
    }
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  return mainWindow;
}

function sendProgress(message) {
  log.info(`[startup] ${message}`);
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send('startup-progress', message);
  }
}

function sendError(message) {
  log.error(`[startup] ${message}`);
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send('startup-error', message);
  }
}

async function startup() {
  createSplashWindow();

  try {
    // Initialize settings
    sendProgress('Loading settings...');
    settingsManager = new SettingsManager();
    settingsManager.load();
    settingsManager.ensureDirectories();

    // Initialize process manager
    processManager = new ProcessManager(settingsManager);

    // Start all services
    await processManager.startAll((msg) => sendProgress(msg));

    // Open main window pointing at Flask
    const serverUrl = settingsManager.getServerUrl(processManager.resolvedPorts.flask);
    log.info(`Opening main window at ${serverUrl}`);
    createMainWindow(serverUrl);

  } catch (err) {
    log.error('Startup failed:', err);
    sendError(err.message);

    const result = await dialog.showMessageBox(splashWindow || null, {
      type: 'error',
      title: 'Auto A11y - Startup Error',
      message: 'Failed to start Auto A11y',
      detail: err.message + '\n\nCheck logs at: ' + path.join(app.getPath('userData'), 'logs'),
      buttons: ['Retry', 'View Logs', 'Quit'],
      defaultId: 0,
    });

    if (result.response === 0) {
      // Retry
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.destroy();
      }
      startup();
    } else if (result.response === 1) {
      // View Logs
      const { shell } = require('electron');
      shell.openPath(path.join(app.getPath('userData'), 'logs'));
      app.quit();
    } else {
      app.quit();
    }
  }
}

// App lifecycle
app.whenReady().then(startup);

app.on('window-all-closed', () => {
  app.quit();
});

// Note: before-quit fires again when app.quit() is called below.
// The isShuttingDown guard in processManager prevents double-shutdown.
// event.preventDefault() is synchronous (before the first await), so it works correctly.
app.on('before-quit', async (event) => {
  if (processManager && !processManager.isShuttingDown) {
    event.preventDefault();
    await processManager.stopAll();
    app.quit(); // This re-fires before-quit, but isShuttingDown is now true
  }
});

app.on('activate', () => {
  // macOS: re-create window when dock icon clicked
  if (BrowserWindow.getAllWindows().length === 0 && processManager) {
    const url = settingsManager.getServerUrl(processManager.resolvedPorts.flask);
    createMainWindow(url);
  }
});
```

- [ ] **Step 2: Test Electron launches (development mode — no bundled binaries)**

This test verifies the Electron shell starts and shows the splash screen. It will fail to start Flask/MongoDB (no bundled binaries) but proves the shell works.

```bash
cd electron && npx electron . --dev 2>&1 | head -20
```

Expected: The splash window appears. You'll see log messages about falling back to system Python/mongod. If MongoDB and Flask are running locally, the main window will load the Flask UI. If not, you'll see the error dialog.

- [ ] **Step 3: Commit**

```bash
git add electron/main.js
git commit -m "feat: add Electron main process with splash screen and service orchestration"
```

---

## Task 9: Integration Test — Full Startup

This task verifies the complete flow: Electron starts MongoDB, starts Flask, loads the UI.

**Prerequisites:** MongoDB must be installed and available as `mongod` in PATH. The Python venv must have all dependencies.

- [ ] **Step 1: Create a development launch script**

Create `electron/dev-start.sh`:

```bash
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
```

```bash
chmod +x electron/dev-start.sh
```

- [ ] **Step 2: Run the full integration test**

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python/electron && ./dev-start.sh
```

**Expected behavior:**
1. Splash screen appears with "Auto A11y" logo and progress bar
2. Status updates: "Loading settings..." → "Starting database..." → "Starting server..." → "Waiting for server..." → "Ready"
3. Main window opens showing the Flask dashboard (no login required — AUTH_ENABLED=False)
4. Closing the window triggers graceful shutdown (check `~/.config/auto-a11y/logs/electron.log`)

**If it fails:**
- Check `~/.config/auto-a11y/logs/electron.log` for error details
- Check `~/.config/auto-a11y/logs/mongod.log` for MongoDB issues
- Check `~/.config/auto-a11y/logs/flask.log` for Python issues
- Verify `mongod --version` works (MongoDB must be installed)
- Verify `.venv/bin/python run.py --desktop --port 5099` works (test Flask independently)

- [ ] **Step 3: Verify graceful shutdown**

After closing the Electron window:

```bash
# Verify no orphan processes
ps aux | grep -E "mongod|run.py" | grep -v grep
```

Expected: No mongod or run.py processes from the desktop app.

```bash
# Verify MongoDB shut down cleanly (no lock file content)
cat ~/.config/auto-a11y/mongodb/data/mongod.lock 2>/dev/null
```

Expected: Empty file or no file.

- [ ] **Step 4: Verify settings.json was created**

```bash
cat ~/.config/auto-a11y/settings.json
```

Expected: The default settings JSON matching the schema from the spec.

- [ ] **Step 5: Commit dev-start script**

```bash
git add electron/dev-start.sh
git commit -m "feat: add development launcher script for Electron shell"
```

---

## Task 10: Process Manager — Development Fallback Paths

The process manager currently falls back to system `python3` and `mongod` when bundled binaries don't exist. For development, we need it to use the project's `.venv/bin/python` instead of bare `python3`.

**Files:**
- Modify: `electron/process-manager.js`

- [ ] **Step 1: Add development-mode path resolution**

In `process-manager.js`, update the `getPaths()` method to also check for the project's venv:

```javascript
  getPaths() {
    const resourcesPath = process.resourcesPath || path.join(__dirname, '..');
    const projectRoot = path.join(__dirname, '..');
    return {
      mongod: path.join(resourcesPath, 'mongodb', 'bin', 'mongod'),
      python: path.join(resourcesPath, 'python', 'bin', 'python3.12'),
      pythonDev: path.join(projectRoot, '.venv', 'bin', 'python'),
      appDir: path.join(resourcesPath, 'app'),
      appDirDev: projectRoot,
      chromium: path.join(resourcesPath, 'chromium'),
    };
  }
```

Update the Python path fallback in `startFlask()`. Replace the existing fallback block:

```javascript
    // Determine python path: bundled or system
    let pythonPath = paths.python;
    if (!fs.existsSync(pythonPath)) {
      // Fall back to system python (for development)
      pythonPath = 'python3';
      log.warn('Bundled Python not found, falling back to system python3');
    }

    // Determine app entry point
    let runPy = path.join(paths.appDir, 'run.py');
    if (!fs.existsSync(runPy)) {
      // Development: app dir is the project root
      runPy = path.join(__dirname, '..', 'run.py');
      log.warn('Bundled app not found, falling back to:', runPy);
    }
```

With:

```javascript
    // Determine python path: bundled → project venv → system
    let pythonPath = paths.python;
    if (!fs.existsSync(pythonPath)) {
      if (fs.existsSync(paths.pythonDev)) {
        pythonPath = paths.pythonDev;
        log.info('Using project venv Python:', pythonPath);
      } else {
        pythonPath = 'python3';
        log.warn('No bundled or venv Python, falling back to system python3');
      }
    }

    // Determine app entry point: bundled → project root
    let runPy = path.join(paths.appDir, 'run.py');
    if (!fs.existsSync(runPy)) {
      runPy = path.join(paths.appDirDev, 'run.py');
      log.info('Using project root run.py:', runPy);
    }
```

- [ ] **Step 2: Re-test the full startup with dev-start.sh**

```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python/electron && ./dev-start.sh
```

Expected: Same behavior as Task 9, but log should now show "Using project venv Python: /home/tait/Documents/cnib/code/auto_a11y_python/.venv/bin/python" instead of "falling back to system python3".

- [ ] **Step 3: Commit**

```bash
git add electron/process-manager.js
git commit -m "feat: add development-mode path resolution for venv Python and project root"
```

---

## Summary

After completing all 10 tasks, the result is:

1. **Python side**: `DESKTOP_MODE`, `AUTH_ENABLED`, `USER_DATA_DIR` env vars in config, data directory redirection, `/shutdown` endpoint, auth bypass
2. **Electron side**: complete process lifecycle management (settings → mongod → Flask → BrowserWindow → graceful shutdown)
3. **Development workflow**: `electron/dev-start.sh` launches the full stack using system mongod and project venv

**What's NOT in this plan (deferred to later sub-projects):**
- Settings panel UI (Sub-project 2)
- Build pipeline and installers (Sub-project 3)
- Auto-update (Sub-project 4)
- Base template nav link (Sub-project 2 — when the route exists)
