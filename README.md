# Auto A11y Python

A comprehensive web accessibility testing tool that combines automated DOM testing with AI-powered visual analysis. This is a Python port of the original autoA11y.js Node.js application, maintaining the core JavaScript test scripts while adding enhanced features.

## Overview

Auto A11y Python is a comprehensive web accessibility testing platform that:
- Crawls websites to discover pages automatically using Pyppeteer
- Executes battle-tested JavaScript accessibility tests in browser context
- Integrates Claude AI for visual accessibility analysis beyond DOM testing
- Provides project-based organization for managing multiple websites
- Generates detailed WCAG 2.1 compliance reports in multiple formats

## Features

- **Automated Accessibility Testing**: Comprehensive WCAG 2.1 compliance testing using JavaScript test scripts from the original autoA11y.js
- **AI-Powered Visual Analysis**: Optional Claude AI integration detects visual issues that DOM testing might miss
- **Smart Web Scraping**: Automatic page discovery with robots.txt compliance
- **Browser-Based Testing**: Real DOM testing using Pyppeteer (Python port of Puppeteer)
- **Project Management**: Organize testing across multiple projects and websites
- **Comprehensive Reporting**: Generate HTML, JSON, CSV, and PDF reports
- **Asynchronous Processing**: Background task runner for non-blocking operations
- **Web Interface**: Modern Flask application with Bootstrap 5 UI

## Technology Stack

- **Backend**: Python 3.8+
- **Browser Automation**: Pyppeteer (Puppeteer for Python)
- **Database**: MongoDB 4.0+
- **AI Integration**: Claude AI by Anthropic
- **Web Framework**: Flask with Blueprints
- **Testing Scripts**: JavaScript (preserved from original autoA11y.js)
- **Task Queue**: Async task runner for background jobs

## Prerequisites

- Python 3.8 or higher
- MongoDB 4.0 or higher
- Google Chrome or Chromium browser

## Installation

```bash
# Clone the repository
git clone https://github.com/[your-username]/auto_a11y_python.git
cd auto_a11y_python

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Enable git hooks (translation validation on commit)
git config core.hooksPath .githooks

# Set up MongoDB (if not already running)
# Install MongoDB and start the service
# Default connection: mongodb://localhost:27017/

# Configure the application
cp config.example.py config.py
# Edit config.py with your settings

# Run initial setup
python run.py --setup
```

The setup process will:
- Create necessary directories
- Set up database indexes
- Download Chromium browser for Pyppeteer
- Create a sample project to get started

## Docker

Auto A11y ships with a `docker-compose.yml` that runs the app and
MongoDB together. With Docker (20.10+) or Podman (4.1+) installed:

```bash
docker compose up
# or: podman-compose up
```

The UI is then available at http://localhost:5001.

### Testing host-local servers

To run accessibility tests against an HTTP server running on **your
host machine** (for example a dev server on port `8080`), use the
hostname `host.docker.internal` instead of `localhost`:

| Running on host as | Enter in Auto A11y as |
|---|---|
| `http://localhost:80` | `http://host.docker.internal` |
| `http://localhost:8080` | `http://host.docker.internal:8080` |
| `http://localhost:8000` | `http://host.docker.internal:8000` |

This is enabled by the `extra_hosts` entry in `docker-compose.yml`. No
extra configuration is required. If the host server is bound only to
`127.0.0.1`, rebind it to `0.0.0.0` so the container can reach it.

## Configuration

Edit `config.py` to customize your settings:

```python
# MongoDB settings
MONGO_URI = "mongodb://localhost:27017/"
DATABASE_NAME = "auto_a11y"

# Server settings
HOST = "127.0.0.1"
PORT = 5000
DEBUG = False

# AI settings (optional)
RUN_AI_ANALYSIS = False  # Set to True to enable Claude AI
CLAUDE_API_KEY = "your-anthropic-api-key"

# Directories
SCREENSHOTS_DIR = "screenshots"
REPORTS_DIR = "reports"

# Scraping settings
MAX_DEPTH = 3
MAX_PAGES_PER_WEBSITE = 100
```

### SMTP Email / Password Reset (Optional)

Password reset via email is optional. If the SMTP environment variables are left blank, the "Forgot your password?" link is hidden from the login page and the reset routes return 404. Admins will still see the "Email Password Reset" card on the user edit page, but the button is disabled with a configuration hint.

Add the following to your `.env`:

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=True
SMTP_FROM_EMAIL=noreply@example.com
SMTP_FROM_NAME=CNIB Access Labs | AutoA11y
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `SMTP_HOST` | Yes | *(empty)* | SMTP server hostname |
| `SMTP_PORT` | No | `587` | SMTP server port |
| `SMTP_USERNAME` | No | *(empty)* | SMTP auth username (skip if relay needs no auth) |
| `SMTP_PASSWORD` | No | *(empty)* | SMTP auth password |
| `SMTP_USE_TLS` | No | `True` | Use STARTTLS |
| `SMTP_FROM_EMAIL` | Yes | *(empty)* | Sender email address |
| `SMTP_FROM_NAME` | No | `CNIB Access Labs \| AutoA11y` | Sender display name |

Both `SMTP_HOST` and `SMTP_FROM_EMAIL` must be set for email to be enabled. Once configured:

- A **"Forgot your password?"** link appears on the login page, allowing users to request a reset link (valid for 15 minutes).
- Admins can send a reset email from the **user edit page** (`/auth/users/<id>/edit`).

### Microsoft 365 SSO (Optional)

The login page supports "Sign in with Microsoft" for client users. This is optional -- if the environment variables are left blank, the SSO button is hidden and only email/password login is available.

#### 1. Register an app in Azure AD

1. Go to the [Azure Portal](https://portal.azure.com/) and navigate to **Microsoft Entra ID** (formerly Azure Active Directory) > **App registrations** > **New registration**.
2. Set the following fields:
   - **Name**: whatever you like (e.g. "Auto A11y Public").
   - **Supported account types**: choose **Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant)** to allow users from any Microsoft 365 organization, or choose **Single tenant** if you only want users from your own organization.
   - **Redirect URI**: select **Web** and enter your callback URL. For local development this is `http://localhost:5001/auth/microsoft/callback`. For production, use your real domain (e.g. `https://reports.example.com/auth/microsoft/callback`).
3. Click **Register**.

#### 2. Collect the values you need

After registration, on the app's **Overview** page:

| Value | Where to find it | `.env` variable |
|---|---|---|
| Application (client) ID | Overview page, top | `MICROSOFT_CLIENT_ID` |
| Directory (tenant) ID | Overview page, top (only needed for single-tenant; use `common` for multi-tenant) | `MICROSOFT_TENANT_ID` |

#### 3. Create a client secret

1. Go to **Certificates & secrets** > **Client secrets** > **New client secret**.
2. Give it a description and expiry period, then click **Add**.
3. Copy the **Value** immediately (it is only shown once).

| Value | `.env` variable |
|---|---|
| Client secret value | `MICROSOFT_CLIENT_SECRET` |

#### 4. API permissions

The default registration already grants the `User.Read` delegated permission under Microsoft Graph, which is all that is needed. Verify this under **API permissions** -- you should see `Microsoft Graph > User.Read` listed. No admin consent is required for this permission.

#### 5. Add to your `.env`

```bash
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-secret-value
MICROSOFT_TENANT_ID=common
```

Set `MICROSOFT_TENANT_ID` to `common` (the default) for multi-tenant, or to your specific tenant ID for single-tenant.

#### 6. Verify

Start the app and visit `/auth/login`. The "Sign in with Microsoft" button should appear above the email/password form.

### Google SSO (Optional)

The login page also supports "Sign in with Google". Like Microsoft SSO, this is optional -- if the environment variables are left blank, the Google button is hidden.

#### 1. Create a project in Google Cloud Console

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project (or select an existing one).
2. Navigate to **APIs & Services** > **OAuth consent screen**.
3. Choose **External** user type (allows any Google account) or **Internal** (restricts to your Google Workspace organization only). Click **Create**.
4. Fill in the required fields:
   - **App name**: whatever you like (e.g. "Auto A11y Public").
   - **User support email**: your email address.
   - **Developer contact information**: your email address.
5. Click **Save and Continue**.
6. On the **Scopes** screen, click **Add or Remove Scopes** and add:
   - `openid`
   - `email`
   - `profile`
7. Click **Save and Continue** through the remaining screens.

#### 2. Create OAuth credentials

1. Navigate to **APIs & Services** > **Credentials**.
2. Click **Create Credentials** > **OAuth client ID**.
3. Set **Application type** to **Web application**.
4. Give it a name (e.g. "Auto A11y Public").
5. Under **Authorized redirect URIs**, add your callback URL. For local development this is `http://localhost:5001/auth/google/callback`. For production, use your real domain (e.g. `https://reports.example.com/auth/google/callback`).
6. Click **Create**.

#### 3. Collect the values you need

After creation, a dialog shows your credentials:

| Value | `.env` variable |
|---|---|
| Client ID | `GOOGLE_CLIENT_ID` |
| Client secret | `GOOGLE_CLIENT_SECRET` |

You can also find these later under **APIs & Services** > **Credentials** by clicking on the OAuth client you created.

#### 4. Add to your `.env`

```bash
GOOGLE_CLIENT_ID=xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx
```

#### 5. Verify

Start the app and visit `/auth/login`. The "Sign in with Google" button should appear above the email/password form.

## Usage

### Starting the Application

```bash
# Run with default settings
python run.py

# Run with options
python run.py --host 0.0.0.0 --port 8080 --debug

# Other commands
python run.py --test-db          # Test database connection
python run.py --download-browser  # Download Chromium
python run.py --setup            # Run initial setup
```

### Web Interface

Open your browser to `http://localhost:5000`

#### Workflow:

1. **Create a Project**: Organize your testing efforts
2. **Add Websites**: Add websites to test within projects
3. **Discover Pages**: Automatically crawl and discover pages
4. **Run Tests**: Execute accessibility tests on pages
5. **Generate Reports**: Export detailed compliance reports

## JavaScript Test Scripts

The core accessibility tests are JavaScript modules executed in the browser context:

- **headings.js**: Heading hierarchy and structure
- **images.js**: Alt text and decorative images
- **forms.js & forms2.js**: Form labels and accessibility
- **landmarks.js**: ARIA landmarks and regions
- **colorContrast.js**: WCAG color contrast ratios
- **focus.js**: Keyboard navigation and focus
- **language.js**: Language declarations
- **pageTitle.js**: Page title requirements
- **tabindex.js**: Tab order and keyboard access
- **ariaRoles.js**: ARIA attribute validation
- **svg.js**: SVG accessibility
- **pdf.js**: PDF link detection

## AI Analysis Features

When Claude AI is enabled, the system detects:

- **Visual Headings**: Text that looks like headings but lacks semantic markup
- **Reading Order**: Mismatches between visual and DOM order
- **Modal Accessibility**: Issues with dialogs and overlays
- **Language Changes**: Unmarked foreign language content
- **Motion & Animation**: Problematic animations
- **Interactive Elements**: Custom controls lacking ARIA

## Project Structure

```
auto_a11y_python/
├── auto_a11y/
│   ├── core/            # Core functionality
│   │   ├── browser_manager.py
│   │   ├── database.py
│   │   └── scraper.py
│   ├── models/          # Data models
│   ├── scripts/         # JavaScript test files
│   │   └── tests/       # Individual test modules
│   ├── testing/         # Test runner
│   │   ├── test_runner.py
│   │   ├── script_injector.py
│   │   └── result_processor.py
│   ├── ai/              # Claude AI integration
│   │   ├── claude_analyzer.py
│   │   └── analysis_modules.py
│   ├── reporting/       # Report generation
│   │   ├── report_generator.py
│   │   └── formatters.py
│   └── web/            # Flask application
│       ├── app.py
│       └── routes/
├── templates/          # HTML templates
├── static/            # CSS, JS, images
├── docs/              # Documentation
├── Fixtures/          # Test fixtures for validation
├── config.py          # Configuration
├── test_fixtures.py   # Fixture testing script
└── run.py            # Entry point
```

## Testing

### Fixture Testing

Auto A11y includes a comprehensive fixture testing system to validate that accessibility tests work correctly before deploying them in production. For complete documentation:

- **[Fixture Testing Guide](docs/FIXTURE_TESTING.md)** - Comprehensive guide with workflows and examples
- **[Quick Reference](FIXTURE_TESTING_QUICKREF.md)** - Command cheat sheet for daily use

**Quick start:**
```bash
# Test all fixtures (takes ~1 hour for ~900 fixtures)
python test_fixtures.py

# Test only Discovery fixtures (~5 minutes)
python test_fixtures.py --type Disco

# Test specific category
python test_fixtures.py --category Images

# Test specific error code
python test_fixtures.py --code ErrNoAlt

# Combine filters
python test_fixtures.py --type Err --category Headings --limit 10
```

Only tests that pass ALL their fixtures are enabled in production. View fixture status at `http://localhost:5001/testing/fixture-status`

## Type Checking

This project enforces strict static type checking via three complementary tools: **mypy**, **pyright**, and **ty**. All three must pass on every commit and in CI.

### One-time setup (per clone)

After cloning:
```bash
python run.py --install-hooks
```

This configures `git` to use the repo's `.githooks/` directory. The pre-commit hook runs all three type checkers against the scoped paths and blocks the commit on any failure.

### Running checks manually

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pyright
.venv/bin/python -m ty check
```

### Policy

No `# type: ignore` or equivalent suppression comments. No `cast(Any, ...)` workarounds. No `git commit --no-verify`. Type errors are fixed, not suppressed. See [CLAUDE.md](./CLAUDE.md#type-checking-mandatory) for the full policy.

## API Usage

The application provides REST API endpoints for automation:

```python
# Example: Test a page
POST /api/pages/{page_id}/test
{
    "include_ai": true,
    "take_screenshot": true
}

# Example: Generate report
POST /api/reports/generate
{
    "type": "website",
    "id": "website_id",
    "format": "html",
    "include_ai": true
}

# Example: Discover pages
POST /api/websites/{website_id}/discover
{
    "max_depth": 3,
    "max_pages": 50
}
```

## Troubleshooting

### Common Issues

1. **MongoDB Connection Error**
   ```bash
   # Ensure MongoDB is running
   mongod
   # Test connection
   python run.py --test-db
   ```

2. **Browser Download Failed**
   ```bash
   # Manual download
   python run.py --download-browser
   # Or install Chrome/Chromium system-wide
   ```

3. **JavaScript Tests Not Loading**
   - Check that all files exist in `/auto_a11y/scripts/tests/`
   - Review browser console for errors
   - Ensure Pyppeteer is properly installed

4. **AI Analysis Not Working**
   - Verify Claude API key in config.py
   - Check API rate limits
   - Ensure `RUN_AI_ANALYSIS = True` in config

## Migration

Export and import the MongoDB database between environments (e.g. cloud ↔ local) using `mongodump` and `mongorestore`.

### Export (dump) from source

```bash
# From local MongoDB (default)
mongodump --db auto_a11y --out ./dump

# From a remote/cloud MongoDB (with connection string)
mongodump --uri "mongodb+srv://user:password@cluster.example.net/" --db auto_a11y --out ./dump
```

This creates a `./dump/auto_a11y/` directory containing BSON files for every collection.

### Import (restore) to target

```bash
# To local MongoDB (default)
mongorestore --db auto_a11y --drop ./dump/auto_a11y

# To a remote/cloud MongoDB
mongorestore --uri "mongodb+srv://user:password@cluster.example.net/" --db auto_a11y --drop ./dump/auto_a11y
```

The `--drop` flag drops each collection before restoring, ensuring a clean import. Omit it to merge into existing data.

### Common workflows

```bash
# Cloud → Local
mongodump --uri "$CLOUD_MONGODB_URI" --db auto_a11y --out ./dump
mongorestore --db auto_a11y --drop ./dump/auto_a11y

# Local → Cloud
mongodump --db auto_a11y --out ./dump
mongorestore --uri "$CLOUD_MONGODB_URI" --db auto_a11y --drop ./dump/auto_a11y
```

If your database name differs from the default `auto_a11y`, replace it with the value of `DATABASE_NAME` from your `.env` or `config.py`.

### Prerequisites

Install the [MongoDB Database Tools](https://www.mongodb.com/docs/database-tools/installation/installation/) (`mongodump` and `mongorestore`). These are separate from the MongoDB server and must be installed individually on most systems.

## Desktop Application Builds

Auto A11y can be packaged as a standalone desktop application using Electron. The desktop build bundles a portable Python runtime, a sidecar MongoDB instance, and Playwright Chromium — no external dependencies required on the target machine.

### Architecture

The Electron shell (`electron/main.js`) acts as a thin wrapper:

1. Starts an embedded **MongoDB** sidecar (`mongod`)
2. Starts the **Flask** server using a bundled portable Python
3. Opens a BrowserWindow pointing at the local Flask server
4. Gracefully shuts down both services on exit

All four components (server, database, browser, LLM) are switchable between internal (bundled) and external (user-provided) via an in-app settings panel. Settings are stored in the platform's standard user data directory.

### Prerequisites (all platforms)

- **Node.js 18+** and **npm**
- **curl** (for downloading portable Python and MongoDB)
- An internet connection during the build (to download dependencies)

### Linux (AppImage)

```bash
# 1. Install Electron dependencies (first time only)
cd electron
npm install
cd ..

# 2. Run the build script
chmod +x build/build-linux.sh
./build/build-linux.sh
```

The script:
1. Downloads [portable Python 3.12](https://github.com/indygreg/python-build-standalone) (x86_64)
2. Installs pip dependencies from `requirements.txt` into the portable Python
3. Downloads MongoDB 7.0 Community (just the `mongod` binary)
4. Downloads Playwright Chromium via the portable Python
5. Copies application source to `build/staging/`
6. Packages everything with `electron-builder` into an AppImage

**Output:** `electron/dist/Auto A11y-<version>.AppImage`

Run the AppImage directly — no installation needed:
```bash
chmod +x "electron/dist/Auto A11y-1.0.0.AppImage"
./"electron/dist/Auto A11y-1.0.0.AppImage"
```

### macOS (DMG)

```bash
# 1. Install native dependencies for WeasyPrint PDF generation
brew install cairo pango gdk-pixbuf gobject-introspection libffi

# 2. Install Electron dependencies (first time only)
cd electron
npm install
cd ..

# 3. Run the build script
chmod +x build/build-mac.sh
./build/build-mac.sh
```

The macOS build has an additional step compared to Linux: it bundles the WeasyPrint native libraries (dylibs) from Homebrew into the app and rewrites their install names with `@loader_path` so the app works without Homebrew on the target machine. A `python3.12-wrapper` script sets `DYLD_LIBRARY_PATH` at runtime.

The script auto-detects the architecture (`arm64` for Apple Silicon, `x86_64` for Intel).

**Output:** `electron/dist/Auto A11y-<version>.dmg`

### Windows

There is no Windows build script yet. The Electron shell and process manager already handle Windows paths (including `taskkill` for process cleanup), but the build pipeline has not been implemented.

A Windows build script would need to:
1. Download [portable Python for Windows](https://github.com/indygreg/python-build-standalone) (`x86_64-pc-windows-msvc`)
2. Install pip dependencies
3. Download [MongoDB Community for Windows](https://www.mongodb.com/try/download/community) (just `mongod.exe`)
4. Download Playwright Chromium
5. Handle WeasyPrint's GTK3 runtime dependency on Windows (e.g., bundle from MSYS2/vcpkg or use the `weasyprint` wheel with bundled DLLs)
6. Package with `electron-builder --win` (produces NSIS installer or portable EXE)

### Development Mode

To run the Electron shell in development mode (using your system's MongoDB and the project's Python venv instead of bundled binaries):

```bash
cd electron
npm install   # first time only
./dev-start.sh
# or: npx electron . --dev
```

This requires MongoDB and the Python venv (`.venv/`) to already be set up on your machine. The process manager falls back to system-installed `mongod` and `.venv/bin/python` when bundled binaries are not found.

### Build Output Structure

The packaged app bundles these resources alongside the Electron binary:

```
resources/
├── app/            # Application source (auto_a11y/, config.py, run.py, Fixtures/)
├── python/         # Portable Python 3.12 + pip dependencies
│   └── bin/
│       ├── python3.12
│       └── python3.12-wrapper  # macOS only: sets DYLD_LIBRARY_PATH
├── mongodb/
│   └── bin/
│       └── mongod              # MongoDB 7.0 server binary
└── chromium/                   # Playwright-managed Chromium browser
```

### User Data

At runtime, the app stores its data in the platform's user data directory:

| Platform | Location |
|----------|----------|
| Linux | `~/.config/auto-a11y/` |
| macOS | `~/Library/Application Support/auto-a11y/` |
| Windows | `%APPDATA%\auto-a11y\` |

Contents: `settings.json`, `mongodb/data/` (database files), `logs/`, `reports/`, `screenshots/`.

## Colour System

This application uses a **custom design token system** for all colours. Bootstrap colour utility classes (e.g., `btn-primary`, `bg-danger`, `text-warning`) are **not used** — instead, custom classes map directly to design tokens defined in `auto_a11y/web/static/public/css/tokens.css`.

This gives full control over all colours in light mode, dark mode, and print, with WCAG 2.2 AA contrast compliance built in. See the `Colour System` section in `CLAUDE.md` for the complete class reference.

**Key files:**
- `auto_a11y/web/static/public/css/tokens.css` — design tokens (all colour values)
- `auto_a11y/web/static/css/style.css` — custom utility classes

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

GNU General Public License v3.0

## Author

Bob Dodd

## Acknowledgments

- Original autoA11y.js Node.js application for the JavaScript test suite
- Pyppeteer team for the Python port of Puppeteer
- Claude AI by Anthropic for visual accessibility analysis
- Flask and MongoDB communities

## Support

For issues, questions, or contributions, please use the GitHub issue tracker.
