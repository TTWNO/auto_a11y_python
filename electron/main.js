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
