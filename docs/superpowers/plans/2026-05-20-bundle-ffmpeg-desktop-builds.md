# Bundle ffmpeg into Desktop Builds — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle `ffmpeg` + `ffprobe` static binaries into the macOS DMG (and the Linux AppImage / Windows installer build scripts, not auto-run in CI) so a fresh desktop install can process video with no manual binary install.

**Architecture:** Each build script downloads version-pinned, SHA-256-verified static binaries into `build/staging/ffmpeg/bin/` and electron-builder copies that into the app's `Resources/ffmpeg/`. The Electron process manager prepends the bundled `ffmpeg/bin` dir to the Flask child process's `PATH`, so `auto_a11y/audio/ffmpeg.py::detect_ffmpeg`'s `shutil.which("ffmpeg")` resolves the bundled binary with no Python change. macOS code-signing of the binaries is automatic — `electron/afterPack.js` already walks all of `Contents/Resources/`.

**Tech Stack:** Bash build scripts, electron-builder, evermeet.cx / johnvansickle.com / BtbN ffmpeg static builds, Electron (`process-manager.js`).

**Spec:** [`docs/superpowers/specs/2026-05-20-bundle-ffmpeg-desktop-builds-design.md`](../specs/2026-05-20-bundle-ffmpeg-desktop-builds-design.md)

**Branch:** `bundle-ffmpeg-desktop` (off `audioA11y-integration`; spec committed at `cb5a06a9`, `9ee58102`).

---

## Pre-flight

- [ ] **P.1 — Activate venv + confirm branch.**
```bash
cd /home/tait/Documents/cnib/code/auto_a11y_python
source .venv/bin/activate
git status   # On branch bundle-ffmpeg-desktop, clean
git log --oneline audioA11y-integration..HEAD   # two spec commits
```

- [ ] **P.2 — Note the build environment limitation.** These build scripts run on macOS (`build-mac.sh`) and Linux (`build-linux.sh`); the dev box here is Linux. You CANNOT run `build-mac.sh` to completion here (no macOS, no `hdiutil`/`codesign`). That's fine — Phase 0 is verified by **shell-syntax check + a dry-run of the download step in isolation**, not a full DMG build. The full DMG build + install smoke test is owed to the macOS release machine and is called out in Phase 3 as owner-side.

---

## Phase 0 — macOS DMG (the exercised build)

**Files:**
- Modify: `build/build-mac.sh` — add ffmpeg download step + extraResources entry + existence checks.
- Modify: `electron/process-manager.js` — add `ffmpegBin` path + PATH prepend (shared by all platforms).

### Task 0.1 — Add the ffmpeg download step to build-mac.sh

The existing MongoDB download (`build-mac.sh:186-203`) is the pattern. Add an analogous step after the Chromium step (after line 212), before "Step 6. Copy application source".

- [ ] **Step 1: Add version + checksum constants** near the existing version block (`build-mac.sh:11-14`). Insert after `MONGO_VERSION="7.0.17"`:

```bash
# ffmpeg: evermeet.cx static builds (x86_64; runs under Rosetta 2 on Apple
# Silicon — see spec "Apple Silicon"). Pin to a dated release + verify SHA.
# Evermeet serves the latest at a stable URL AND keeps dated archives; we use
# the dated archive so a future upstream rebuild can't silently change bytes.
FFMPEG_MAC_VERSION="7.1"
# NOTE: capture the actual SHA-256 of each archive at implementation time via
#   curl -L <url> | shasum -a 256
# and paste below. The build MUST fail on mismatch.
FFMPEG_MAC_SHA256="REPLACE_WITH_REAL_SHA256"
FFPROBE_MAC_SHA256="REPLACE_WITH_REAL_SHA256"
```

> **Implementer note:** evermeet.cx's exact archive-URL scheme must be confirmed at implementation time (it serves `https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip` for "latest" and `https://evermeet.cx/ffmpeg/ffmpeg-<version>.zip` for dated). Pick the dated form, capture the real SHA-256, and replace the placeholders. If evermeet's dated URLs are unreliable, fall back to the `getrelease` latest URL but STILL pin a captured SHA so the build fails if bytes change.

- [ ] **Step 2: Add the download function** after the Chromium step (after `build-mac.sh:212`):

```bash
# -------------------------------------------------------
# 5b. Download ffmpeg + ffprobe (static, for audioA11y video pipeline)
# -------------------------------------------------------
echo ""
echo "--- Step 5b: ffmpeg + ffprobe $FFMPEG_MAC_VERSION ---"
mkdir -p "$BUILD_DIR/ffmpeg/bin"

download_and_verify() {
    # $1 = url, $2 = expected sha256, $3 = output path
    local url="$1" expected="$2" out="$3"
    curl -L -o "$out" "$url"
    local actual
    actual="$(shasum -a 256 "$out" | awk '{print $1}')"
    if [ "$actual" != "$expected" ]; then
        echo "ERROR: SHA-256 mismatch for $url" >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        exit 1
    fi
}

FFMPEG_ZIP="$BUILD_DIR/ffmpeg-mac.zip"
FFPROBE_ZIP="$BUILD_DIR/ffprobe-mac.zip"
download_and_verify \
    "https://evermeet.cx/ffmpeg/ffmpeg-${FFMPEG_MAC_VERSION}.zip" \
    "$FFMPEG_MAC_SHA256" "$FFMPEG_ZIP"
download_and_verify \
    "https://evermeet.cx/ffmpeg/ffprobe-${FFMPEG_MAC_VERSION}.zip" \
    "$FFPROBE_MAC_SHA256" "$FFPROBE_ZIP"

unzip -o "$FFMPEG_ZIP" -d "$BUILD_DIR/ffmpeg/bin"
unzip -o "$FFPROBE_ZIP" -d "$BUILD_DIR/ffmpeg/bin"
chmod +x "$BUILD_DIR/ffmpeg/bin/ffmpeg" "$BUILD_DIR/ffmpeg/bin/ffprobe"

# Smoke test: the binary must run on the build host. (On an Apple-Silicon
# build host this exercises Rosetta; on Intel it's native.)
"$BUILD_DIR/ffmpeg/bin/ffmpeg" -version | head -1
"$BUILD_DIR/ffmpeg/bin/ffprobe" -version | head -1

# License file for GPL compliance.
cat > "$BUILD_DIR/ffmpeg/LICENSE.txt" <<'FFMPEGLIC'
This product bundles FFmpeg (https://ffmpeg.org), a static build from
evermeet.cx, licensed under the GNU General Public License v3.
FFmpeg is invoked as a subprocess and is not linked into Auto A11y.
Source for the bundled FFmpeg build is available from https://ffmpeg.org
and https://evermeet.cx/ffmpeg/.
FFMPEGLIC

echo "ffmpeg + ffprobe staged at $BUILD_DIR/ffmpeg/bin/"
```

- [ ] **Step 3: Add to the generated extraResources.** In the `build-config.json` heredoc (`build-mac.sh:311-315`), add the ffmpeg line:

```json
  "extraResources": [
    { "from": "$BUILD_DIR/python", "to": "python" },
    { "from": "$BUILD_DIR/app", "to": "app" },
    { "from": "$BUILD_DIR/mongodb", "to": "mongodb" },
    { "from": "$BUILD_DIR/chromium", "to": "chromium" },
    { "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" }
  ],
```

- [ ] **Step 4: Add existence checks** to the post-build audit. After the chromium check (`build-mac.sh:395-397`), add:

```bash
[ -f "$RESOURCES_IN_DMG/ffmpeg/bin/ffmpeg" ] \
    || missing_paths+=("ffmpeg/bin/ffmpeg")
[ -f "$RESOURCES_IN_DMG/ffmpeg/bin/ffprobe" ] \
    || missing_paths+=("ffmpeg/bin/ffprobe")
```

- [ ] **Step 5: Update the `mkdir -p` line** at `build-mac.sh:35` to pre-create the ffmpeg dir (cosmetic; the download step also mkdir's it):

```bash
mkdir -p "$BUILD_DIR"/{python,app,mongodb/bin,chromium,ffmpeg/bin}
```

- [ ] **Step 6: Shell-syntax check** (can't run the full build on Linux):

```bash
bash -n build/build-mac.sh && echo "syntax OK"
```

- [ ] **Step 7: Commit.**

```bash
git add build/build-mac.sh
git commit --no-gpg-sign -m "$(cat <<'EOF'
build(mac): bundle ffmpeg + ffprobe into the DMG

Adds a download-and-verify step (evermeet.cx static x86_64 builds,
SHA-256 pinned) staging ffmpeg + ffprobe to build/staging/ffmpeg/bin/,
adds the tree to electron-builder extraResources, and adds post-build
existence checks. afterPack.js already signs everything under
Contents/Resources/, so no signing change is needed. GPL LICENSE.txt
bundled alongside.

x86_64 binary runs under Rosetta 2 on Apple Silicon; universal lipo
build is a noted follow-up. SHA-256 placeholders must be filled with
real captured hashes on the macOS release machine.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 0.2 — Electron PATH prepend (shared across all platforms)

**File:** `electron/process-manager.js`

- [ ] **Step 1: Add `ffmpegBin` to the paths object** (`process-manager.js:25-33`). After the `chromium` line:

```js
      chromium: path.join(resourcesPath, 'chromium'),
      ffmpegBin: path.join(resourcesPath, 'ffmpeg', 'bin'),
```

- [ ] **Step 2: Prepend to PATH in the env block.** After the chromium block (`process-manager.js:233-236`), add:

```js
    // Prepend the bundled ffmpeg/ffprobe dir so the audio pipeline's
    // detect_ffmpeg() (shutil.which) resolves the bundled binary first.
    if (fs.existsSync(paths.ffmpegBin)) {
      const sep = process.platform === 'win32' ? ';' : ':';
      env.PATH = paths.ffmpegBin + sep + (env.PATH || process.env.PATH || '');
    }
```

- [ ] **Step 3: Syntax check.**

```bash
node --check electron/process-manager.js && echo "syntax OK"
```

- [ ] **Step 4: Commit.**

```bash
git add electron/process-manager.js
git commit --no-gpg-sign -m "$(cat <<'EOF'
electron: prepend bundled ffmpeg/bin to the Flask process PATH

When resources/ffmpeg/bin exists in the packaged app, prepend it to the
spawned Python process's PATH (platform-correct separator). The audio
pipeline's detect_ffmpeg() then resolves the bundled binary via
shutil.which with no Python change. Mirrors the existing
PLAYWRIGHT_BROWSERS_PATH wiring. Dev runs (system ffmpeg on PATH) are
unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 — Linux AppImage

**Files:** Modify `build/build-linux.sh`.

The PATH prepend (Phase 0 Task 0.2) is shared — no JS change here.

### Task 1.1 — Add ffmpeg download to build-linux.sh

- [ ] **Step 1: Add version + SHA constants** near `build-linux.sh:11-14`:

```bash
# ffmpeg: johnvansickle.com static amd64 build. Pinned + SHA-verified.
FFMPEG_LINUX_VERSION="release"   # johnvansickle ships a single "release" tarball
FFMPEG_LINUX_SHA256="REPLACE_WITH_REAL_SHA256"
```

> **Implementer note:** johnvansickle ships `ffmpeg-release-amd64-static.tar.xz` (rolling) plus `ffmpeg-N.N-amd64-static.tar.xz` dated archives. Prefer a dated archive; capture its SHA-256. The tarball contains BOTH `ffmpeg` and `ffprobe` in one directory, unlike the macOS two-zip layout.

- [ ] **Step 2: Add the download step** after the Chromium step (after `build-linux.sh:79`):

```bash
# -------------------------------------------------------
# 5b. Download ffmpeg + ffprobe (static)
# -------------------------------------------------------
echo ""
echo "--- Step 5b: ffmpeg + ffprobe (linux static) ---"
mkdir -p "$BUILD_DIR/ffmpeg/bin"
FFMPEG_TARBALL="$BUILD_DIR/ffmpeg-linux.tar.xz"
FFMPEG_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-${FFMPEG_LINUX_VERSION}-amd64-static.tar.xz"
curl -L -o "$FFMPEG_TARBALL" "$FFMPEG_URL"
actual="$(sha256sum "$FFMPEG_TARBALL" | awk '{print $1}')"
if [ "$actual" != "$FFMPEG_LINUX_SHA256" ]; then
    echo "ERROR: ffmpeg SHA-256 mismatch (expected $FFMPEG_LINUX_SHA256, got $actual)" >&2
    exit 1
fi
# The tarball has a top-level versioned dir containing ffmpeg + ffprobe.
tar -xJf "$FFMPEG_TARBALL" -C "$BUILD_DIR/ffmpeg/bin" --strip-components=1 \
    --wildcards '*/ffmpeg' '*/ffprobe'
chmod +x "$BUILD_DIR/ffmpeg/bin/ffmpeg" "$BUILD_DIR/ffmpeg/bin/ffprobe"
"$BUILD_DIR/ffmpeg/bin/ffmpeg" -version | head -1
cat > "$BUILD_DIR/ffmpeg/LICENSE.txt" <<'FFMPEGLIC'
This product bundles FFmpeg (https://ffmpeg.org), a static build from
johnvansickle.com, licensed under the GNU General Public License v3.
FFmpeg is invoked as a subprocess and is not linked into Auto A11y.
FFMPEGLIC
echo "ffmpeg + ffprobe staged at $BUILD_DIR/ffmpeg/bin/"
```

- [ ] **Step 3: Add to extraResources** (`build-linux.sh:122-126`):

```json
    { "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" }
```

- [ ] **Step 4: Update `mkdir -p`** at `build-linux.sh:22` to add `ffmpeg/bin`.

- [ ] **Step 5: Syntax check + dry-run the download in isolation** (Linux host CAN run this part):

```bash
bash -n build/build-linux.sh && echo "syntax OK"
# Optional real download dry-run (captures the SHA to paste back):
# curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | sha256sum
```

- [ ] **Step 6: Commit.**

```bash
git add build/build-linux.sh
git commit --no-gpg-sign -m "build(linux): bundle ffmpeg + ffprobe into the AppImage

johnvansickle static amd64 build, SHA-pinned, staged to
build/staging/ffmpeg/bin/ and added to extraResources. Shares the
Electron PATH-prepend from Phase 0. Not auto-run in CI (prep).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Windows installer (NEW build script)

**Files:** Create `build/build-windows.sh`.

This is the largest unknown — there is no existing Windows build. The acceptance bar for THIS sub-project is a **best-effort, syntax-valid script that correctly stages ffmpeg + generates an electron-builder NSIS config**; the portable-Python-on-Windows story may be left as a documented TODO if it balloons (per the spec).

### Task 2.1 — Scaffold build-windows.sh

- [ ] **Step 1: Create `build/build-windows.sh`** modelled on `build-linux.sh`. Reuse the same structure (staging dir, version constants, app copy, extraResources, electron-builder invocation). The Windows-specific parts:
  - Portable Python: python-build-standalone ships `x86_64-pc-windows-msvc-install_only` builds — use that URL form. **If the embeddable/standalone Windows Python doesn't `pip install` the native deps (torch etc.) cleanly, mark this step `TODO_WINDOWS_PYTHON` and surface it.**
  - mongod: `https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-${MONGO_VERSION}.zip`.
  - ffmpeg: BtbN `ffmpeg-master-latest-win64-gpl.zip` (pin to a dated release tag, not `latest`), SHA-verified. Contains `bin/ffmpeg.exe` + `bin/ffprobe.exe`.
  - electron-builder target: `"win": { "target": "nsis" }`.
  - extraResources includes `{ "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" }`.

- [ ] **Step 2: ffmpeg download block** (the part that MUST be correct even if Python staging is TODO):

```bash
# ffmpeg + ffprobe (BtbN win64 gpl static)
mkdir -p "$BUILD_DIR/ffmpeg/bin"
FFMPEG_WIN_TAG="REPLACE_WITH_DATED_BTBN_TAG"   # e.g. autobuild-2026-05-01-...
FFMPEG_WIN_SHA256="REPLACE_WITH_REAL_SHA256"
FFMPEG_ZIP="$BUILD_DIR/ffmpeg-win.zip"
curl -L -o "$FFMPEG_ZIP" \
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_WIN_TAG}/ffmpeg-master-latest-win64-gpl.zip"
actual="$(sha256sum "$FFMPEG_ZIP" | awk '{print $1}')"
[ "$actual" = "$FFMPEG_WIN_SHA256" ] || { echo "ffmpeg SHA mismatch" >&2; exit 1; }
# BtbN zip has ffmpeg-*/bin/{ffmpeg,ffprobe}.exe — extract just those.
unzip -j -o "$FFMPEG_ZIP" '*/bin/ffmpeg.exe' '*/bin/ffprobe.exe' \
    -d "$BUILD_DIR/ffmpeg/bin"
cat > "$BUILD_DIR/ffmpeg/LICENSE.txt" <<'FFMPEGLIC'
This product bundles FFmpeg (https://ffmpeg.org), a static build from
BtbN/FFmpeg-Builds, licensed under the GNU General Public License v3.
FFmpeg is invoked as a subprocess and is not linked into Auto A11y.
FFMPEGLIC
```

- [ ] **Step 3: Syntax check.**

```bash
bash -n build/build-windows.sh && echo "syntax OK"
```

- [ ] **Step 4: Commit.**

```bash
git add build/build-windows.sh
git commit --no-gpg-sign -m "build(windows): NEW build-windows.sh skeleton with ffmpeg bundling

Net-new Windows build script (NSIS via electron-builder). ffmpeg +
ffprobe from BtbN win64-gpl, SHA-pinned, staged to
build/staging/ffmpeg/bin/. Windows portable-Python staging marked
TODO_WINDOWS_PYTHON if native deps don't install cleanly. NOT auto-run
in CI — prep for the future.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — Credit + final verification

**Files:** Modify `auto_a11y/web/templates/about.html` (the Fluent-based About page — primary), `auto_a11y/web/translations/{en,fr}/common.ftl`, and `electron/splash.html` (bonus splash credit).

### Task 3.1 — ffmpeg credit (Fluent About page + splash)

A Flask-side About page **does exist**: `auto_a11y/web/templates/about.html`. It extends `base.html`, uses the `common-*` Fluent prefix, and already has a "WCAG Data Attribution" section — the conventional home for a third-party-software credit. (It is currently orphaned — no `/about` route links to it — but the credit belongs there per the spec and will be live the moment it's routed. Wiring a route is an out-of-scope follow-up.)

Per the spec's i18n obligation, the About-page credit MUST be a Fluent message in EN + FR.

- [ ] **Step 1: Add the Fluent strings** to BOTH `common.ftl` files. In `auto_a11y/web/translations/en/common.ftl`:

```ftl
common-third-party-software = Third-party software
common-this-application-bundles-ffmpeg = This application bundles FFmpeg (https://ffmpeg.org), licensed under the GNU General Public License v3, used to process audio and video recordings. FFmpeg is invoked as a separate program and is not linked into Auto A11y.
```

In `auto_a11y/web/translations/fr/common.ftl` (direct French):

```ftl
common-third-party-software = Logiciels tiers
common-this-application-bundles-ffmpeg = Cette application intègre FFmpeg (https://ffmpeg.org), sous licence GNU General Public License v3, utilisé pour traiter les enregistrements audio et vidéo. FFmpeg est appelé comme un programme distinct et n'est pas lié au code d'Auto A11y.
```

- [ ] **Step 2: Add a "Third-party software" section to `about.html`.** After the WCAG Data Attribution `</section>` (find it via `grep -n "wcag-attribution-heading" auto_a11y/web/templates/about.html`), add a parallel section using the existing card markup pattern:

```html
            <!-- Third-party software attribution -->
            <section aria-labelledby="third-party-heading">
                <div class="card mb-4">
                    <div class="card-header">
                        <h2 id="third-party-heading" class="mb-0"><i class="bi bi-file-text" aria-hidden="true"></i> {{ ftl('common-third-party-software') }}</h2>
                    </div>
                    <div class="card-body">
                        <p class="mb-0">{{ ftl('common-this-application-bundles-ffmpeg') }}</p>
                    </div>
                </div>
            </section>
```

Match the surrounding indentation + card classes (structural Bootstrap only; no colour utilities). Confirm the heading level continues the page's hierarchy (the existing sections use `h2` inside `card-header`).

- [ ] **Step 3: Validate translations.**

```bash
source .venv/bin/activate
python tests/validate_translations.py
```

Expected: PASS (every EN id has an FR id).

- [ ] **Step 4: Add the bonus splash credit** to `electron/splash.html` near the `.version` div (`splash.html:75`). The splash is Electron-static (NOT Flask-rendered), so it's outside the Fluent system — a plain English credit is acceptable here as a supplement, not a substitute for the Fluent About-page credit:

```html
  <div class="version">Accessibility Testing Platform</div>
  <div class="credits" style="font-size: 11px; opacity: 0.7; margin-top: 8px;">
    Bundles FFmpeg (GPLv3) · MongoDB · Chromium
  </div>
```

- [ ] **Step 5: Commit.**

```bash
git add auto_a11y/web/templates/about.html \
        auto_a11y/web/translations/en/common.ftl \
        auto_a11y/web/translations/fr/common.ftl \
        electron/splash.html
git commit --no-gpg-sign -m "$(cat <<'EOF'
feat: credit bundled FFmpeg on the About page (Fluent EN+FR) + splash

Primary credit lives in about.html (the existing Fluent-based About
page) as a "Third-party software" section, with common-third-party-*
strings in EN + FR per the bilingual rule. about.html is currently
orphaned (no /about route) — routing it is an out-of-scope follow-up,
but the credit is ready when it lands.

Bonus: a plain-English credit line on the Electron splash, which is
Electron-static (outside the Flask Fluent system) so a literal is
acceptable there.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.2 — Owner-side verification checklist (macOS release machine)

This sub-project's code is verifiable on Linux only up to syntax + the Linux download dry-run. The DMG end-to-end test is owed to the macOS release machine. Document it in the final report; do NOT mark the branch "done" claiming a DMG test that didn't run here.

- [ ] **Step 1: Write the verification steps into the PR description** (not executed here):
  1. On the macOS release machine, fill the real SHA-256 placeholders in `build-mac.sh` (capture via `curl -L <url> | shasum -a 256`).
  2. Run `build/build-mac.sh`; confirm the build's own existence + signature audit passes (it now checks `ffmpeg/bin/{ffmpeg,ffprobe}`).
  3. Install the DMG on an Intel Mac AND an Apple Silicon Mac (the latter needs Rosetta 2 — present on all modern macOS).
  4. Paste API keys when Settings Recovery prompts (this is the one manual step).
  5. Upload a 30-second fixture MP4; confirm the pipeline runs to completion — Settings Recovery must NOT fire for ffmpeg.
  6. Confirm the splash shows the FFmpeg credit and `Resources/ffmpeg/LICENSE.txt` is present.

- [ ] **Step 2: Full pre-commit gate one more time** (the Python type checks etc. — even though this branch barely touches Python):

```bash
source .venv/bin/activate
.venv/bin/python -m mypy && .venv/bin/python -m pyright 2>&1 | grep -E "errors|warnings"
bun run scripts/check-css-a11y.ts 2>&1 | tail -2
```

These should be unaffected (no Python/CSS changes), confirming we didn't break the gate.

---

## Out of scope (deferred)

- macOS universal (arm64+x64) ffmpeg via `lipo` — only if Rosetta perf is unacceptable.
- CI automation for Linux/Windows builds.
- MP4Box bundling.
- pyannote model bundling.
- Wiring a `/about` route to the existing (orphaned) `about.html` — the ffmpeg credit is added to that template now (Task 3.1), but making it reachable is a separate follow-up.

## When you're done

- [ ] Push: `git push -u origin bundle-ffmpeg-desktop`.
- [ ] Open a PR against `audioA11y-integration` (this branch stacks on it) — NOT against `main`, since the parent branch isn't merged yet. Note the dependency in the PR description.
- [ ] PR description includes the Task 3.2 owner-side macOS verification checklist and is explicit that the DMG end-to-end test was NOT run in the dev environment (no macOS).

## Stop points

If you find yourself wanting to:
- Bake API keys into a build
- Bundle the pyannote model
- Add a CI workflow that runs the Linux/Windows builds
- Hardcode an unverified ffmpeg download (no SHA pin)

— stop and surface to the human. The spec rules these out.
