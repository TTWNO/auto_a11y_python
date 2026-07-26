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
    claude_model: 'claude-opus-4-8',
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
      env.CLAUDE_MODEL = s.llm.claude_model || 'claude-opus-4-8';
    } else {
      env.RUN_AI_ANALYSIS = 'False';
    }

    // Browser
    if (s.browser.mode === 'internal') {
      env.BROWSER_MODE = 'local';
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
