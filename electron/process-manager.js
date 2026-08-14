const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { app } = require('electron');
const log = require('electron-log');

// How long to wait for Flask's /health before giving up. The bundled
// app imports a large dependency graph (Flask + reporting + the audio
// pipeline's torch/pyannote stack) under an embedded Python, and on the
// FIRST launch macOS Gatekeeper also scans the freshly de-quarantined
// resources. Observed cold-start to "Serving Flask app" is ~30-32s even
// when warm; the old 30s budget timed out 2-3s before Flask was ready
// and surfaced a spurious "failed to start". 120s leaves comfortable
// headroom for a cold first launch without masking a genuine hang
// (MongoDB is already verified up before we get here).
const FLASK_HEALTH_TIMEOUT_MS = 120000;

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
   * In development, these may not exist — callers check and fall back.
   */
  getPaths() {
    const resourcesPath = process.resourcesPath || path.join(__dirname, '..');
    const projectRoot = path.join(__dirname, '..');
    const isWindows = process.platform === 'win32';

    /* The Windows layouts differ in two ways that are easy to miss.
     * python-build-standalone's windows-msvc archive puts the
     * interpreter at python\python.exe with no bin\ directory at all,
     * and every bundled executable carries a .exe suffix. Resolving the
     * Unix layout on Windows fails at launch, having reported nothing
     * more useful than a missing file. */
    const exe = isWindows ? '.exe' : '';
    const python = isWindows
      ? path.join(resourcesPath, 'python', 'python.exe')
      : path.join(resourcesPath, 'python', 'bin', 'python3.12');

    return {
      mongod: path.join(resourcesPath, 'mongodb', 'bin', `mongod${exe}`),
      /* macOS only: a shell wrapper that sets DYLD_LIBRARY_PATH before
       * exec'ing the interpreter. Windows finds its DLLs on PATH. */
      pythonWrapper: path.join(resourcesPath, 'python', 'bin', 'python3.12-wrapper'),
      python,
      pythonDev: isWindows
        ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
        : path.join(projectRoot, '.venv', 'bin', 'python'),
      appDir: path.join(resourcesPath, 'app'),
      appDirDev: projectRoot,
      chromium: path.join(resourcesPath, 'chromium'),
      ffmpegBin: path.join(resourcesPath, 'ffmpeg', 'bin'),
      /* WeasyPrint loads cairo/pango through ctypes, which searches
       * PATH on Windows. The GTK3 runtime ships beside the app. */
      gtkBin: isWindows ? path.join(resourcesPath, 'gtk', 'bin') : null,
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
              // 'healthy'  → the full app is up.
              // 'recovery' → Settings Recovery mode: a required preflight
              //   check failed, so Flask is serving ONLY the recovery
              //   page (and answers /health with 503 + status 'recovery').
              //   That still means the server is up and reachable, so we
              //   resolve and let the window open — it lands on /recovery/
              //   where the user can fix the configuration. Treating it as
              //   "not ready" would poll until timeout and surface a dead
              //   "failed to start" dialog, leaving the recovery UI
              //   permanently unreachable.
              if (data.status === 'healthy' || data.status === 'recovery') {
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
  async cleanStaleLock() {
    const dbPath = path.join(this.settings.userDataDir, 'mongodb', 'data');
    const lockFile = path.join(dbPath, 'mongod.lock');

    if (!fs.existsSync(lockFile)) return;

    const content = fs.readFileSync(lockFile, 'utf8').trim();
    if (!content) return; // Empty lock = clean shutdown

    const pid = parseInt(content, 10);
    const clear = () => {
      try {
        fs.writeFileSync(lockFile, '', 'utf8');
        log.info('Cleared mongod.lock');
      } catch (err) {
        log.warn('Could not clear mongod.lock:', err.message);
      }
    };

    // Is the recorded owner still alive?
    let alive = false;
    if (Number.isInteger(pid) && pid > 0) {
      try { process.kill(pid, 0); alive = true; } catch { alive = false; }
    }

    if (!alive) {
      // Owner gone — just a stale file from an unclean shutdown.
      log.warn(`Found stale mongod.lock (PID ${content}); owner is gone, clearing.`);
      clear();
      return;
    }

    // A process still holds OUR dbpath. If it's a leftover mongod — a
    // crashed/orphaned previous launch, or a second app instance — a fresh
    // mongod would die with DBPathInUse (exit 100) and the app would "fail
    // to start". Terminate the leftover so this launch can recover. Guard
    // against PID reuse: only kill if it actually looks like mongod.
    if (this.isMongodPid(pid)) {
      log.warn(`A mongod (PID ${pid}) still holds the dbpath; terminating it to recover startup...`);
      await this.terminatePid(pid);
      clear();
    } else {
      log.warn(`mongod.lock PID ${pid} is held by a non-mongod process; leaving it alone.`);
    }
  }

  /**
   * Best-effort check that `pid` is a mongod process. Guards terminatePid
   * against killing an unrelated process that happened to reuse a dead
   * mongod's PID. Returns false (don't kill) if it can't positively
   * identify mongod.
   */
  isMongodPid(pid) {
    try {
      if (process.platform === 'win32') {
        const out = execSync(`tasklist /fi "PID eq ${pid}" /fo csv /nh`, {
          encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
        });
        return /mongod/i.test(out);
      }
      const out = execSync(`ps -p ${pid} -o comm=`, {
        encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
      });
      return /mongod/i.test(out);
    } catch {
      return false;
    }
  }

  /**
   * SIGTERM a pid, escalate to SIGKILL if it hasn't exited within graceMs,
   * then wait briefly for the OS to release its file locks. Resolves once
   * the process is gone (or was never alive). Never throws.
   */
  async terminatePid(pid, { graceMs = 4000, killMs = 2000, stepMs = 100 } = {}) {
    const alive = () => { try { process.kill(pid, 0); return true; } catch { return false; } };
    if (!alive()) return;

    try { process.kill(pid, 'SIGTERM'); } catch { /* already gone */ }
    let deadline = Date.now() + graceMs;
    while (Date.now() < deadline && alive()) {
      await new Promise((r) => setTimeout(r, stepMs));
    }

    if (alive()) {
      try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ }
      deadline = Date.now() + killMs;
      while (Date.now() < deadline && alive()) {
        await new Promise((r) => setTimeout(r, stepMs));
      }
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

    await this.cleanStaleLock();

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

    // Prepend the bundled ffmpeg/ffprobe dir so the audio pipeline's
    // detect_ffmpeg() (shutil.which) resolves the bundled binary first.
    const pathSep = process.platform === 'win32' ? ';' : ':';
    if (fs.existsSync(paths.ffmpegBin)) {
      env.PATH = paths.ffmpegBin + pathSep + (env.PATH || process.env.PATH || '');
    }

    // WeasyPrint reaches cairo and pango through ctypes, which resolves
    // them from PATH on Windows — the macOS build does the equivalent
    // with DYLD_LIBRARY_PATH in its python wrapper. Without this the
    // reports that use WeasyPrint fail at import, not at render.
    if (paths.gtkBin && fs.existsSync(paths.gtkBin)) {
      env.PATH = paths.gtkBin + pathSep + (env.PATH || process.env.PATH || '');
      log.info('Prepended bundled GTK runtime for WeasyPrint:', paths.gtkBin);
    }

    // Determine python path: wrapper (macOS) → bundled → project venv → system
    // The wrapper sets DYLD_LIBRARY_PATH for WeasyPrint native libs on macOS
    let pythonPath = paths.python;
    if (process.platform === 'darwin' && fs.existsSync(paths.pythonWrapper)) {
      pythonPath = paths.pythonWrapper;
      log.info('Using macOS Python wrapper for WeasyPrint dylibs');
    } else if (!fs.existsSync(pythonPath)) {
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

    // Poll /health until ready. Recovery mode counts as "up" (the server
    // is serving the recovery page); see pollUrl.
    const healthUrl = `http://127.0.0.1:${port}/health`;
    onProgress && onProgress('Waiting for server...');
    const health = await this.pollUrl(healthUrl, FLASK_HEALTH_TIMEOUT_MS);
    if (health && health.status === 'recovery') {
      log.warn(
        `Flask started in Settings Recovery mode on port ${port} — a required ` +
        `check failed (e.g. MongoDB unreachable or ffmpeg/ffprobe missing). ` +
        `Opening the recovery page so the user can fix the configuration.`
      );
      onProgress && onProgress('Configuration needed — opening settings...');
    } else {
      log.info(`Flask is ready on port ${port}`);
    }
  }

  /**
   * Strip com.apple.quarantine from bundled Resources on macOS.
   *
   * Why this exists: an ad-hoc-signed DMG arrives with the quarantine
   * extended attribute set on every file. Right-clicking → Open the
   * outer .app de-quarantines that one bundle, but Gatekeeper re-evaluates
   * each *nested* bundle on first launch. Playwright's full GUI Chromium
   * forks helper bundles (Google Chrome for Testing Helper.app, Helper
   * (GPU).app, Helper (Renderer).app, Helper (Plugin).app) — these are
   * each separate bundles whose quarantine xattr is still set, and macOS
   * refuses to launch them. This breaks manual-login mode specifically,
   * because it is the only flow that uses the GUI Chromium; the
   * single-binary chromium-headless-shell used by every other test
   * doesn't fork helper bundles and is unaffected.
   *
   * `xattr -dr` is recursive and idempotent: a no-op on files without
   * the attribute, so safe to run on every launch. We scope it to
   * resourcesPath (chromium + python + mongodb sidecars) rather than
   * the whole app to keep wall time bounded on machines with cold
   * filesystem caches.
   */
  async unquarantineResources() {
    if (process.platform !== 'darwin') return;
    const resourcesPath = process.resourcesPath;
    if (!resourcesPath || !fs.existsSync(resourcesPath)) return;
    // Skip in dev: extraResources don't exist when running `npm start`.
    if (!fs.existsSync(path.join(resourcesPath, 'chromium'))) return;

    log.info('Stripping com.apple.quarantine from bundled resources');
    await new Promise((resolve) => {
      const proc = spawn('xattr', ['-dr', 'com.apple.quarantine', resourcesPath], {
        stdio: 'ignore',
      });
      proc.on('exit', (code) => {
        if (code !== 0) {
          log.warn(`xattr -dr exited with code ${code} (continuing)`);
        }
        resolve();
      });
      proc.on('error', (err) => {
        log.warn('xattr failed (continuing):', err.message);
        resolve();
      });
    });
  }

  /**
   * Start all internal services in order.
   */
  async startAll(onProgress) {
    await this.unquarantineResources();
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
