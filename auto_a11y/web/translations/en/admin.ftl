# Admin settings — superadmin-only system configuration UI.

admin-settings-title = System Settings
admin-settings-intro = Configure integrations and feature flags. Values saved here override matching environment variables.
admin-settings-save = Save changes
admin-settings-clear = Clear settings
admin-settings-toc-label = Settings sections

# Source-of-truth indicators on each section.
admin-settings-source-database = These values are loaded from the database and override any environment variables.
admin-settings-source-environment = No values are saved here. The current configuration comes from environment variables; saving below will override them.
admin-settings-source-unset = This integration is not configured. Saving values below will enable it.
admin-settings-source-default = No values are saved here and no environment variables are set. The application is using built-in defaults; saving below will override them.

# Application secrets — stored in the per-user settings file (read before the
# database is available). Mongo URI, optional API keys, ffmpeg overrides.
admin-settings-user-secrets-heading = Application secrets
admin-settings-user-secrets-description = Connection and API-key values used at startup and by optional features. These are stored in this computer's local settings file, separate from the database settings above.
admin-settings-user-secrets-restart-note = Saved values apply to new work right away. Restart the application to apply them everywhere.
admin-settings-user-secrets-mongodb-uri = MongoDB connection URI
admin-settings-user-secrets-mongodb-uri-help = Leave blank to use the bundled/default database connection.
admin-settings-user-secrets-anthropic = Anthropic API key
admin-settings-user-secrets-anthropic-help = Enables AI analysis features.
admin-settings-user-secrets-deepgram = Deepgram API key
admin-settings-user-secrets-deepgram-help = Enables audio transcription features.
admin-settings-user-secrets-huggingface = Hugging Face token
admin-settings-user-secrets-huggingface-help = Optional; used for speaker-diarization models.
admin-settings-user-secrets-ffmpeg = ffmpeg path (optional override)
admin-settings-user-secrets-ffmpeg-help = Leave blank to use the bundled ffmpeg or the one on your PATH.
admin-settings-user-secrets-ffprobe = ffprobe path (optional override)
admin-settings-user-secrets-ffprobe-help = Leave blank to use the bundled ffprobe or the one on your PATH.
admin-settings-user-secrets-secret-set = A value is already stored — leave blank to keep it.
admin-settings-user-secrets-secret-unset = No value is stored yet.
admin-settings-user-secrets-placeholder-set = •••••••• (stored)
admin-settings-user-secrets-remove = Remove the stored value
admin-settings-user-secrets-saved = Application secrets saved.
admin-settings-user-secrets-save-failed = Could not write the settings file: { $detail }

# Generic helpers shared across env-var sections.
admin-settings-restart-note = Most changes take effect immediately. A few settings (e.g. server bind address, scheduler startup) only apply after restarting the application.
admin-settings-password-placeholder-set = (leave blank to keep existing value)
admin-settings-password-help-set = A value is currently saved. Enter a new value to replace it, or leave blank to keep it.
admin-settings-password-help-unset = Stored in the database; not displayed once saved.
admin-settings-section-saved = { $heading } settings saved.
admin-settings-section-cleared = { $heading } settings cleared. The application will fall back to environment variables.
admin-settings-section-clear-confirm = Clear the saved { $heading } settings? The application will fall back to environment variables.
admin-settings-unknown-section = Unknown settings section.
admin-settings-field-invalid = { $label }: { $detail }

# Drupal section.
admin-settings-drupal-heading = Drupal Sync
admin-settings-drupal-description = Connection settings for the Drupal JSON:API used to sync discovered pages, recordings, and issues.
admin-settings-drupal-base-url = Base URL
admin-settings-drupal-base-url-help = The root URL of the Drupal site, including https://.
admin-settings-drupal-username = Username
admin-settings-drupal-password = Password
admin-settings-drupal-password-placeholder-set = (leave blank to keep existing password)
admin-settings-drupal-password-help-set = A password is currently saved. Enter a new password to replace it, or leave blank to keep it.
admin-settings-drupal-password-help-unset = Required. Stored in the database; not displayed once saved.
admin-settings-drupal-enabled = Drupal sync enabled
admin-settings-drupal-saved = Drupal settings saved.
admin-settings-drupal-cleared = Drupal settings cleared. The application will fall back to environment variables.
admin-settings-drupal-clear = Clear settings
admin-settings-drupal-clear-confirm = Clear the saved Drupal settings? The application will fall back to environment variables.
admin-settings-drupal-invalid-url = Drupal base URL must start with http:// or https://.
admin-settings-drupal-username-required = Drupal username is required.
admin-settings-drupal-password-required = Drupal password is required.

# Claude AI section.
admin-settings-claude-heading = Claude AI
admin-settings-claude-description = Anthropic Claude credentials and tuning for AI-powered visual accessibility analysis.
admin-settings-claude-api-key = API key
admin-settings-claude-api-key-help = Issued by Anthropic. Required when AI analysis is enabled.
admin-settings-claude-model = Model
admin-settings-claude-model-help = Claude model identifier (e.g. claude-opus-4-20250514).
admin-settings-claude-max-tokens = Max output tokens
admin-settings-claude-budget-tokens = Thinking budget tokens
admin-settings-claude-temperature = Temperature
admin-settings-claude-use-thinking = Use extended thinking
admin-settings-run-ai-analysis = Run AI analysis on tested pages
admin-settings-run-ai-analysis-help = When off, only DOM-based JavaScript tests run. The API key is still required if you re-enable this later.

# Browser section.
admin-settings-browser-heading = Browser Automation
admin-settings-browser-description = Playwright settings for the headless Chromium that runs accessibility tests.
admin-settings-browser-mode = Mode
admin-settings-browser-mode-help = "local" runs Chromium on this machine, "remote" defers to a worker, "disabled" turns browser testing off.
admin-settings-browser-headless = Run headless
admin-settings-browser-timeout = Timeout (ms)
admin-settings-browser-timeout-help = How long to wait for navigation and operations before failing.
admin-settings-browser-viewport-width = Viewport width (px)
admin-settings-browser-viewport-height = Viewport height (px)

# Scraping section.
admin-settings-scraping-heading = Crawling & Scraping
admin-settings-scraping-description = Limits and politeness controls for the page-discovery crawler.
admin-settings-max-pages-per-site = Maximum pages per site
admin-settings-max-crawl-depth = Maximum crawl depth
admin-settings-request-delay = Request delay (seconds)
admin-settings-request-delay-help = Pause between page fetches. Higher values are gentler on the target site.
admin-settings-user-agent = User agent
admin-settings-respect-robots-txt = Respect robots.txt

# Testing section.
admin-settings-testing-heading = Testing & Workers
admin-settings-testing-description = Parallelism, timeouts, and developer-mode toggles for the test runner.
admin-settings-parallel-tests = Parallel tests
admin-settings-test-timeout = Test timeout (ms)
admin-settings-test-timeout-help = Maximum time a single page test may take before being aborted.
admin-settings-max-test-workers = Maximum test workers
admin-settings-worker-stagger-seconds = Worker stagger (seconds)
admin-settings-show-error-codes = Show error codes in reports
admin-settings-show-error-codes-help = Developer mode — exposes raw error codes alongside human descriptions.
admin-settings-pages-per-page = Pages per page (UI)
admin-settings-max-pages-per-page = Maximum pages per page (UI)

# Scheduler section.
admin-settings-scheduler-heading = Scheduler
admin-settings-scheduler-description = APScheduler settings for recurring/scheduled accessibility tests.
admin-settings-scheduler-enabled = Scheduler enabled
admin-settings-scheduler-timezone = Timezone
admin-settings-scheduler-timezone-help = IANA timezone name (e.g. America/Toronto). Affects when scheduled tests fire.
admin-settings-scheduler-max-instances = Maximum job instances
admin-settings-scheduler-misfire-grace-time = Misfire grace time (seconds)
admin-settings-scheduler-misfire-grace-time-help = How late a job can fire before it is skipped as a misfire.
admin-settings-scheduler-coalesce = Coalesce missed runs

# Network / rate-limit section.
admin-settings-network-heading = Network & Rate Limiting
admin-settings-network-description = CORS allowlist and request rate limits for public endpoints.
admin-settings-cors-origins = CORS allowed origins
admin-settings-cors-origins-help = Comma-separated list of origins. Leave blank to disable CORS.
admin-settings-ratelimit-default = Default rate limit
admin-settings-ratelimit-default-help = Flask-Limiter expression, e.g. "60/minute".

# SMTP section.
admin-settings-smtp-heading = SMTP Email
admin-settings-smtp-description = Outgoing-mail settings used for password resets and transactional notifications.
admin-settings-smtp-host = Host
admin-settings-smtp-port = Port
admin-settings-smtp-username = Username
admin-settings-smtp-password = Password
admin-settings-smtp-use-tls = Use TLS
admin-settings-smtp-from-email = From email
admin-settings-smtp-from-name = From name

# Microsoft SSO section.
admin-settings-microsoft-sso-heading = Microsoft SSO
admin-settings-microsoft-sso-description = Microsoft Entra ID / Azure AD application credentials.
admin-settings-microsoft-client-id = Client ID
admin-settings-microsoft-client-secret = Client secret
admin-settings-microsoft-tenant-id = Tenant ID
admin-settings-microsoft-tenant-id-help = Use "common" for any Microsoft account, or your tenant GUID for organisation-only access.

# Google SSO section.
admin-settings-google-sso-heading = Google SSO
admin-settings-google-sso-description = Google OAuth client credentials.
admin-settings-google-client-id = Client ID
admin-settings-google-client-secret = Client secret

# PDF section.
admin-settings-pdf-heading = PDF Auditing
admin-settings-pdf-description = Storage paths, size limits, and helper binaries for the PDF accessibility checker.
admin-settings-pdf-storage-dir = Storage directory
admin-settings-pdf-storage-dir-help = Where downloaded PDFs are kept. Relative paths are resolved against the project root.
admin-settings-pdf-max-size-mb = Maximum PDF size (MB)
admin-settings-pdf-download-timeout-seconds = Download timeout (seconds)
admin-settings-pdf-audit-max-parallel = Maximum parallel PDF audits
