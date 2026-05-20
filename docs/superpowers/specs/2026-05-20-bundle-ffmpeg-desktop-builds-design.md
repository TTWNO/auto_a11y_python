# Bundle ffmpeg into Desktop Builds — Design Spec

**Date:** 2026-05-20
**Branch:** `bundle-ffmpeg-desktop` (off `audioA11y-integration`)
**Status:** Draft

## Goal

Make the desktop app click-and-go for video processing by bundling `ffmpeg` + `ffprobe` into every platform installer (macOS DMG, Linux AppImage, Windows installer). After this work, a fresh install can transcribe + analyse an MP4 with **no manual binary install** — the only first-run setup is pasting API keys (handled by the existing Settings Recovery flow, Phase 10 of the audioA11y integration).

## What "bundle everything" means here

| Dependency | Status |
|---|---|
| Portable Python runtime | Already bundled (`build/build-mac.sh:62-63` installs `requirements.txt` — incl. torch / pyannote.audio / deepgram-sdk / scikit-learn from the audioA11y Phase 4) |
| `mongod` | Already bundled (downloaded from fastdl.mongodb.org) |
| Chromium | Already bundled (`playwright install`) |
| App source tree | Already bundled |
| **`ffmpeg` + `ffprobe`** | **This sub-project** |
| Deepgram + Anthropic API keys | **Deliberately NOT bundled** — secrets are extractable from any distributed binary. First-run entry via Settings Recovery; persisted to the user-settings file. (User decision, 2026-05-20.) |
| pyannote/embedding model (~1 GB) | **Deliberately NOT bundled** — runtime HuggingFace download with graceful degrade to skip-remap (Phase 4/6 already handles absence). (User decision, 2026-05-20.) |

So this spec's scope is exactly: **add the ffmpeg + ffprobe binaries to all three platform builds.** Everything else click-and-go needs is already in place or intentionally out.

## Non-goals

- No MP4Box bundling. Phase 9 callouts already fall back to ffmpeg `-map_chapters` (FFMETADATA1) when MP4Box is absent. (User decision.)
- **No CI automation for the Linux or Windows builds.** The build scripts are written so the capability exists, but Linux/Windows builds are run manually / are prep for the future. macOS DMG is the only build exercised by this sub-project. (User decision, 2026-05-20.)
- No model bundling, no API-key baking.
- No change to the audioA11y pipeline logic — only build packaging + one Electron env wiring line.

## Current state (verified before writing this spec)

- `build/build-mac.sh` is the working macOS DMG build. It stages a portable Python (installs `requirements.txt`), downloads `mongod`, installs Chromium via Playwright, copies the app tree, generates `electron/build-config.json` with an `extraResources` array, runs `electron-builder --mac`, then performs a post-build signature audit + explicit per-resource existence checks (`build-mac.sh:385-397`).
- `build/build-linux.sh` is the Linux AppImage build (parallel structure; no signing).
- There is **no `build/build-windows.sh`** — Windows packaging is net-new in this sub-project.
- `electron/afterPack.js` recursively signs **every** Mach-O under `Contents/Resources/` (ad-hoc when no Developer ID; deferred to electron-builder's own pass when `AUTO_A11Y_SKIP_ADHOC_SIGN=1`). It walks the whole Resources tree, so binaries placed under `Resources/ffmpeg/bin/` are signed automatically — **no afterPack change needed.**
- `electron/process-manager.js` builds the Flask child-process `env` at lines 229-264. It already sets `env.PLAYWRIGHT_BROWSERS_PATH` from the bundled `chromium` path (lines 234-235) and resolves `resourcesPath` (line 23). This is where ffmpeg's bin dir gets prepended to `env.PATH`.
- `auto_a11y/audio/ffmpeg.py::detect_ffmpeg` finds the binary via `shutil.which(...)` (checks `PATH`). It also supports an `override` kwarg and `raise_if_missing`. Preflight checks (`run_ffmpeg_check` / `run_ffprobe_check`) are registered against the central registry, so a missing/broken bundled ffmpeg routes to Settings Recovery.

## Binary layout (installed app)

| Platform | Path |
|---|---|
| macOS | `Auto A11y.app/Contents/Resources/ffmpeg/bin/ffmpeg` + `ffprobe` |
| Linux AppImage | `<mount>/resources/ffmpeg/bin/ffmpeg` + `ffprobe` |
| Windows | `<install>\resources\ffmpeg\bin\ffmpeg.exe` + `ffprobe.exe` |

Staging dir (build time): `build/staging/ffmpeg/bin/` (matches the existing `build/staging/{python,app,mongodb,chromium}` layout the `extraResources` config points at).

## Download sources

Each build script downloads from a **version-pinned URL** and verifies a **SHA-256** before extracting. Version + hash are constants near the top of each script so a host compromise can't silently swap binaries.

| Platform | Source | Arch | License |
|---|---|---|---|
| macOS | `https://evermeet.cx/ffmpeg/` (ffmpeg + ffprobe, separate archives) | The evermeet builds are x86_64; on Apple Silicon they run under Rosetta 2. **Universal is not offered by evermeet** — see "Apple Silicon" below. | GPL v3, static |
| Linux | `https://johnvansickle.com/ffmpeg/releases/` (`ffmpeg-release-amd64-static.tar.xz`) | x86_64 | GPL v3, static |
| Windows | `https://github.com/BtbN/FFmpeg-Builds/releases` (`ffmpeg-master-latest-win64-gpl.zip` → pin to a dated release, not `latest`) | x86_64 | GPL v3, static |

### Apple Silicon

evermeet.cx ships x86_64-only. Two options, decided at implementation time:
1. **Ship x86_64 ffmpeg, run under Rosetta 2** (simplest; works on all Macs since the rest of the app already requires Rosetta-capable hardware for some deps — verify). The transcoding is I/O + network bound (Deepgram upload) so the Rosetta penalty on the local ffmpeg segment/encode step is acceptable for an audit tool.
2. **Build a universal binary** by downloading both an arm64 build (from a source like `osxexperts.net` or building from source) and `lipo`-merging with the x86_64 one. More work; defer unless Rosetta proves a problem.

**This spec chooses option 1** (x86_64 + Rosetta) for the macOS phase, with option 2 noted as a follow-up if performance is unacceptable. The implementation phase verifies the bundled x86_64 ffmpeg actually launches on an Apple Silicon test machine (Rosetta 2 must be installed — it is on all modern macOS, but the preflight check will catch its absence and route to recovery).

## Build script changes

### macOS — `build/build-mac.sh`

1. Add a `download_ffmpeg()` function parallel to the existing mongo download:
   - Pinned URL + SHA-256 for ffmpeg and ffprobe.
   - Download to `$BUILD_DIR/ffmpeg/bin/`, `chmod +x`, verify with `ffmpeg -version` (a smoke test the build fails on).
   - Copy the ffmpeg `LICENSE`/`COPYING` to `$BUILD_DIR/ffmpeg/LICENSE.txt`.
2. Add to the generated `build-config.json` `extraResources`:
   ```json
   { "from": "$BUILD_DIR/ffmpeg", "to": "ffmpeg" }
   ```
3. Add an existence check to the post-build audit (alongside the mongod / chromium checks at `build-mac.sh:385-397`):
   ```bash
   [ -f "$RESOURCES_IN_DMG/ffmpeg/bin/ffmpeg" ]  || missing_paths+=("ffmpeg/bin/ffmpeg")
   [ -f "$RESOURCES_IN_DMG/ffmpeg/bin/ffprobe" ] || missing_paths+=("ffmpeg/bin/ffprobe")
   ```
4. Signing: **no change** — `afterPack.js` already walks all of `Contents/Resources/` and signs every Mach-O, so ffmpeg/ffprobe are covered. The post-build Mach-O audit will verify them too.

### Linux — `build/build-linux.sh`

Same `download_ffmpeg()` step (johnvansickle static tarball), staged to `$BUILD_DIR/ffmpeg/bin/`, added to `extraResources`, existence check in any post-build verification. No signing.

### Windows — `build/build-windows.sh` (NEW)

A new Bash script (runs under Git Bash / WSL / a Windows CI runner — **not auto-run in CI per the non-goals**). Structure mirrors `build-linux.sh`:
- Stage portable Python (Windows embeddable distribution or the same approach the other scripts use — the implementation phase confirms how the portable Python is sourced for Windows; this is the largest unknown).
- Download `mongod.exe`, Chromium, ffmpeg.exe + ffprobe.exe (BtbN zip).
- Generate an electron-builder config with `"win": { "target": "nsis" }` and the same `extraResources`.
- Run `electron-builder --win`.

**Note:** the Windows portable-Python story is the biggest open question — the macOS/Linux scripts use a portable CPython that may not have a clean Windows equivalent in this repo. The implementation phase surfaces this; if it balloons, the Windows build script ships as a documented best-effort skeleton (ffmpeg bundling wired, Python staging marked TODO) rather than blocking the macOS/Linux work.

## Electron wiring — `electron/process-manager.js`

In the `paths` object (line ~23-32), add:
```js
ffmpegBin: path.join(resourcesPath, 'ffmpeg', 'bin'),
```

In the env-building block (line ~229-262), before spawning Flask, prepend the bundled ffmpeg bin dir to `PATH` when it exists:
```js
if (fs.existsSync(paths.ffmpegBin)) {
  const sep = process.platform === 'win32' ? ';' : ':';
  env.PATH = paths.ffmpegBin + sep + (env.PATH || process.env.PATH || '');
}
```

That's the ONLY code change outside build scripts. `detect_ffmpeg()`'s `shutil.which("ffmpeg")` then resolves the bundled binary first. No Python change.

`dev-start.sh` (the non-bundled dev path) is unaffected — developers use system ffmpeg on their PATH, exactly as now.

## License compliance

ffmpeg GPL static builds ship a license file. Each platform bundle includes `resources/ffmpeg/LICENSE.txt`. The app's existing About box gains a line crediting ffmpeg + the bundled version + a pointer to the license file. (GPL redistribution of unmodified static binaries alongside our own separately-licensed app is standard; the binaries are not linked into our code — they're invoked as subprocesses, so this does not impose GPL on auto_a11y itself.)

## Error handling / fallback

If the bundled ffmpeg is somehow missing or unrunnable (corrupt download survived the build smoke-test, Gatekeeper quarantine, missing Rosetta on Apple Silicon), the existing preflight `run_ffmpeg_check` / `run_ffprobe_check` fail and Settings Recovery routes the user to `/recovery/` with a "point at a system ffmpeg" form. So bundling is an optimisation over the recovery flow, not a replacement — the safety net stays.

## Testing

- **macOS (this sub-project's only exercised build):** build the DMG end-to-end; the build script's own existence + signature audit gates it. Manually: install the DMG on both an Intel and an Apple Silicon Mac, upload a 30-second fixture MP4, confirm the pipeline runs to completion without Settings Recovery firing for ffmpeg. Confirm the About box shows the ffmpeg credit.
- **Linux / Windows:** the build scripts are not auto-run; a manual smoke test (run the script, inspect the staged `ffmpeg/bin/`) is the acceptance bar for this sub-project. Full install testing deferred to whenever those platforms ship.
- **No new pytest tests** — this is build packaging + one JS line; there's no Python unit to test. The existing `auto_a11y/audio/ffmpeg.py` detection tests already cover the resolution logic.

## Phase breakdown

| # | Phase | What lands |
|---|---|---|
| 0 | macOS DMG | `download_ffmpeg()` in `build-mac.sh`; `extraResources` entry; post-build existence checks; `process-manager.js` PATH prepend; About-box credit. Verified by a real DMG build + Intel/AS install smoke test. |
| 1 | Linux AppImage | `download_ffmpeg()` in `build-linux.sh`; `extraResources`; existence check. PATH prepend already done in Phase 0 (shared JS). Manual staging smoke test only. |
| 2 | Windows installer | NEW `build-windows.sh` + electron-builder NSIS config + ffmpeg bundling. Windows-style PATH separator already handled by Phase 0's JS. Python-staging story surfaced; best-effort skeleton acceptable if it balloons. NOT run in CI. |
| 3 | Final verification | About-box credits + LICENSE.txt present in each staged bundle; macOS end-to-end pipeline test; Linux/Windows staging inspection. |

**Estimated commits:** ~12-16.

## Risk / rollout

- **Build size:** +~80 MB per platform (ffmpeg + ffprobe static). macOS DMG goes from ~500 MB to ~580 MB. Accepted (user: "bundle everything").
- **Apple Silicon Rosetta dependency** for the x86_64 ffmpeg — mitigated by the preflight check + recovery fallback; universal binary is a noted follow-up.
- **GPL bundling** — compliant for subprocess invocation of unmodified static binaries; LICENSE.txt included; About-box credit added.
- **Windows portable-Python** is the largest unknown; scoped so it can't block the macOS/Linux work.

## Branch + commit policy

- Branch: `bundle-ffmpeg-desktop` off `audioA11y-integration` (stacked).
- **Never** `--no-verify`, **never** `git commit --amend`, **never** rewrite history.
- `--no-gpg-sign` per local convention.
- Activate `.venv` before commits (pyright dep resolution for the hook — even though this sub-project barely touches Python).

## Out of scope (follow-ups)

- macOS universal (arm64+x64) ffmpeg via lipo — only if Rosetta perf is unacceptable.
- CI automation for Linux/Windows builds.
- MP4Box bundling for richer QuickTime chapters.
- Bundling the pyannote model for fully-offline speaker remap.
- Any API-key provisioning automation beyond the existing Settings Recovery first-run entry.
